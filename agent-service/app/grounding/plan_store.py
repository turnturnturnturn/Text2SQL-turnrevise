from __future__ import annotations

import asyncio
import json
from copy import deepcopy
from typing import Any, Protocol

import psycopg
from psycopg.rows import dict_row

from app.grounding.models import GroundingSnapshot
from app.grounding.query_plan import ValidatedQueryPlan


def _safe_plan_json(plan: ValidatedQueryPlan) -> dict[str, Any]:
    payload = plan.plan.model_dump(mode="json", exclude_none=True)
    for ambiguity in payload.get("ambiguities", []):
        ambiguity.pop("message", None)
    for clause in payload.get("filters", []):
        literals = clause.pop("literal_values", [])
        if literals:
            clause["literal_values_redacted"] = True
            clause["literal_value_count"] = len(literals)
    time_range = payload.get("time_range")
    if isinstance(time_range, dict) and time_range.get("preset") == "CUSTOM":
        for key in ("start", "end"):
            value = time_range.pop(key, None)
            if isinstance(value, str):
                time_range[f"{key}_date"] = value[:10]
    return payload


class QueryPlanStore(Protocol):
    async def save(
        self, run_id: str, plan: ValidatedQueryPlan, snapshot: GroundingSnapshot
    ) -> dict[str, Any]: ...

    async def list_for_run(self, run_id: str) -> list[dict[str, Any]]: ...


class InMemoryQueryPlanStore:
    def __init__(self) -> None:
        self._plans: dict[str, list[dict[str, Any]]] = {}
        self._lock = asyncio.Lock()

    async def save(
        self, run_id: str, plan: ValidatedQueryPlan, snapshot: GroundingSnapshot
    ) -> dict[str, Any]:
        async with self._lock:
            existing = self._plans.setdefault(run_id, [])
            for item in existing:
                if item["plan_hash"] == plan.plan_hash:
                    return deepcopy(item)
            record = {
                "sequence_no": len(existing),
                "plan_hash": plan.plan_hash,
                "plan_version": plan.plan_version,
                "validation_status": plan.status.value,
                "plan": _safe_plan_json(plan),
                "grounding_snapshot": snapshot.to_safe_dict(),
                "evidence_ids": plan.evidence_ids,
                "validation_errors": plan.errors,
            }
            existing.append(record)
            return deepcopy(record)

    async def list_for_run(self, run_id: str) -> list[dict[str, Any]]:
        async with self._lock:
            return deepcopy(self._plans.get(run_id, []))


class PostgresQueryPlanStore:
    def __init__(self, database_url: str):
        self.database_url = database_url

    async def _connect(self):
        return await psycopg.AsyncConnection.connect(self.database_url, row_factory=dict_row)

    async def save(
        self, run_id: str, plan: ValidatedQueryPlan, snapshot: GroundingSnapshot
    ) -> dict[str, Any]:
        plan_json = _safe_plan_json(plan)
        grounding_json = snapshot.to_safe_dict()
        async with await self._connect() as connection:
            async with connection.transaction():
                # Serialize sequence allocation per run while allowing plans
                # for different runs to persist concurrently.
                await connection.execute(
                    "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
                    (run_id,),
                )
                existing = await (
                    await connection.execute(
                        """SELECT sequence_no,plan_hash,plan_version,validation_status,
                                  plan_json AS plan,grounding_snapshot,evidence_ids,
                                  validation_errors
                           FROM agent_state.query_plans
                           WHERE run_id=%s AND plan_hash=%s""",
                        (run_id, plan.plan_hash),
                    )
                ).fetchone()
                if existing is not None:
                    return dict(existing)
                row = await (
                    await connection.execute(
                        """INSERT INTO agent_state.query_plans
                           (run_id,sequence_no,plan_version,plan_hash,validation_status,
                            plan_json,grounding_snapshot,evidence_ids,validation_errors)
                           SELECT %s,COALESCE(MAX(sequence_no)+1,0),%s,%s,%s,
                                  %s::jsonb,%s::jsonb,%s,%s::jsonb
                           FROM agent_state.query_plans WHERE run_id=%s
                           RETURNING sequence_no,plan_hash,plan_version,validation_status,
                                     plan_json AS plan,grounding_snapshot,evidence_ids,
                                     validation_errors""",
                        (
                            run_id,
                            plan.plan_version,
                            plan.plan_hash,
                            plan.status.value,
                            json.dumps(plan_json, ensure_ascii=False),
                            json.dumps(grounding_json, ensure_ascii=False),
                            plan.evidence_ids,
                            json.dumps(plan.errors, ensure_ascii=False),
                            run_id,
                        ),
                    )
                ).fetchone()
        assert row is not None
        return dict(row)

    async def list_for_run(self, run_id: str) -> list[dict[str, Any]]:
        async with await self._connect() as connection:
            rows = await (
                await connection.execute(
                    """SELECT sequence_no,plan_hash,plan_version,validation_status,
                              plan_json AS plan,grounding_snapshot,evidence_ids,
                              validation_errors,created_at
                       FROM agent_state.query_plans
                       WHERE run_id=%s ORDER BY sequence_no""",
                    (run_id,),
                )
            ).fetchall()
        return [dict(row) for row in rows]
