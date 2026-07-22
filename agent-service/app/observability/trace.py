from __future__ import annotations

import asyncio
import hashlib
import json
import logging
from copy import deepcopy
from datetime import datetime, timezone
from typing import Any

import psycopg
from psycopg.rows import dict_row
from app.rollout.policy import effective_mode


logger = logging.getLogger(__name__)

TRACE_EVENTS = frozenset({
    "request_received", "context_compiled", "schema_linked", "value_linked",
    "plan_validated", "sql_generated", "guard_decided", "sql_executed",
    "result_verified", "clarification_requested", "run_failed",
    "run_cancelled", "run_completed",
})
ALLOWED_ATTRIBUTES = frozenset({
    "correlation_id", "tenant_id", "route", "risk_level", "model_id",
    "prompt_version", "schema_version", "context_policy_version",
    "grounding_mode", "harness_mode", "status", "duration_ms", "count",
    "db_copilot.context_tokens", "db_copilot.grounding_coverage",
    "db_copilot.guard_decision", "db_copilot.failure_stage",
    "db_copilot.rollout_policy_version", "db_copilot.rollout_cohort",
    "db_copilot.rollout_policy_key", "db_copilot.route_class",
    "db_copilot.safety_execution", "db_copilot.cross_user_leak",
    "db_copilot.provenance_missing",
})


class TracePersistenceError(RuntimeError):
    pass


class InMemoryTraceStore:
    def __init__(self) -> None:
        self._events: dict[str, list[dict[str, Any]]] = {}
        self._lock = asyncio.Lock()

    async def append(self, run_id: str, event: dict[str, Any]) -> dict[str, Any]:
        async with self._lock:
            values = self._events.setdefault(run_id, [])
            stored = deepcopy(event)
            stored["sequence_no"] = len(values) + 1
            values.append(stored)
            return deepcopy(stored)

    async def list_events(self, run_id: str) -> list[dict[str, Any]]:
        async with self._lock:
            return deepcopy(self._events.get(run_id, []))


class PostgresTraceStore:
    def __init__(self, database_url: str) -> None:
        self.database_url = database_url

    async def _connect(self):
        return await psycopg.AsyncConnection.connect(self.database_url, row_factory=dict_row)

    async def append(self, run_id: str, event: dict[str, Any]) -> dict[str, Any]:
        async with await self._connect() as conn:
            async with conn.transaction():
                await conn.execute(
                    "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))", (run_id,)
                )
                row = await (
                    await conn.execute(
                        """INSERT INTO agent_state.run_trace_events
                           (run_id,sequence_no,event_type,attributes,schema_version,
                            instrumentation_version,sampled,created_at)
                           SELECT %s,COALESCE(MAX(sequence_no)+1,1),%s,%s::jsonb,%s,%s,%s,%s
                           FROM agent_state.run_trace_events WHERE run_id=%s
                           RETURNING sequence_no,event_type,attributes,schema_version,
                                     instrumentation_version,sampled,created_at""",
                        (run_id, event["event_type"], json.dumps(event["attributes"]),
                         event["schema_version"], event["instrumentation_version"],
                         event["sampled"], event["created_at"], run_id),
                    )
                ).fetchone()
        result = dict(row)
        result["created_at"] = result["created_at"].isoformat()
        return result

    async def list_events(self, run_id: str) -> list[dict[str, Any]]:
        async with await self._connect() as conn:
            rows = await (
                await conn.execute(
                    """SELECT sequence_no,event_type,attributes,schema_version,
                              instrumentation_version,sampled,created_at
                       FROM agent_state.run_trace_events WHERE run_id=%s
                       ORDER BY sequence_no""", (run_id,)
                )
            ).fetchall()
        result = []
        for row in rows:
            item = dict(row)
            item["created_at"] = item["created_at"].isoformat()
            result.append(item)
        return result


class TraceService:
    allowed_attributes = ALLOWED_ATTRIBUTES

    def __init__(
        self, store, *, mode: str = "shadow", success_sample_rate: float = 0.05,
        schema_version: str = "1.0.0", instrumentation_version: str = "phase-d.1",
        metrics=None,
    ) -> None:
        if mode not in {"off", "shadow", "enforce"}:
            raise ValueError("invalid trace mode")
        if not 0.01 <= success_sample_rate <= 0.10:
            raise ValueError("success sample rate must be between 0.01 and 0.10")
        self.store = store
        self.mode = mode
        self.success_sample_rate = success_sample_rate
        self.schema_version = schema_version
        self.instrumentation_version = instrumentation_version
        self.metrics = metrics

    def should_sample(self, run_id: str, status: str) -> bool:
        if status in {"FAILED", "CANCELLED", "NEEDS_CLARIFICATION", "REJECTED", "SLOW"}:
            return True
        bucket = int.from_bytes(hashlib.sha256(run_id.encode()).digest()[:8], "big") / 2**64
        return bucket < self.success_sample_rate

    async def append(
        self, run_id: str, event_type: str, attributes: dict[str, Any]
    ) -> dict[str, Any] | None:
        if event_type not in TRACE_EVENTS:
            raise ValueError("unsupported trace event type")
        mode = effective_mode("otel", self.mode)
        if mode == "off":
            return None
        safe = {key: value for key, value in attributes.items() if key in ALLOWED_ATTRIBUTES}
        status = str(safe.get("status", ""))
        event = {
            "event_type": event_type,
            "attributes": safe,
            "schema_version": self.schema_version,
            "instrumentation_version": self.instrumentation_version,
            "sampled": self.should_sample(run_id, status),
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        try:
            stored = await self.store.append(run_id, event)
            if self.metrics is not None:
                mode = str(safe.get("grounding_mode") or safe.get("harness_mode") or self.mode)
                if event_type == "guard_decided":
                    self.metrics.increment_guard(
                        "query", str(safe.get("db_copilot.guard_decision", "unknown")), mode
                    )
                elif event_type == "clarification_requested":
                    self.metrics.increment_clarification("ambiguity", "requested")
                elif event_type == "run_completed" and "duration_ms" in safe:
                    self.metrics.observe_run(
                        float(safe["duration_ms"]) / 1000,
                        route=str(safe.get("route", "chat")),
                        risk=str(safe.get("risk_level", "LOW")),
                        status=str(safe.get("status", "COMPLETED")),
                        mode=mode,
                    )
                if "db_copilot.grounding_coverage" in safe:
                    self.metrics.observe_grounding(
                        safe["db_copilot.grounding_coverage"], "all", str(safe.get("schema_version", "current"))
                    )
            return stored
        except Exception as exc:
            logger.exception("local redacted trace persistence failed")
            if mode == "enforce":
                raise TracePersistenceError("local trace persistence failed") from exc
            return None

    async def list_events(self, run_id: str) -> list[dict[str, Any]]:
        return await self.store.list_events(run_id)
