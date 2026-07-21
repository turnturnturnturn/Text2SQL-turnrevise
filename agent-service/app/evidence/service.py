from __future__ import annotations

from typing import Any

from app.harness.models import RunRecord


def _candidate(item: dict[str, Any]) -> dict[str, Any]:
    return {
        key: item[key]
        for key in ("asset_id", "value_id", "column_asset_id", "canonical_value", "source_hash", "score", "reasons")
        if key in item
    }


class EvidenceService:
    """Build the only browser-facing evidence projection from structured state."""

    def __init__(self, plan_store, context_store, business_client, *, mode: str):
        self.plan_store = plan_store
        self.context_store = context_store
        self.business_client = business_client
        self.mode = mode

    async def build(self, run: RunRecord, *, is_admin: bool) -> dict[str, Any]:
        plans = await self.plan_store.list_for_run(run.run_id) if self.plan_store else []
        manifest = await self.context_store.get_manifest(run.run_id) if self.context_store else None
        latest = plans[-1] if plans else None
        snapshot = latest.get("grounding_snapshot", {}) if latest else {}
        plan = latest.get("plan", {}) if latest else {}

        query_plan = None
        validation = None
        if latest:
            query_plan = {
                key: plan.get(key)
                for key in ("metrics", "dimensions", "filters", "time_range", "grain", "sort", "limit", "join_path_ids")
                if key in plan
            }
            query_plan.update({
                "plan_hash": latest.get("plan_hash"),
                "plan_version": latest.get("plan_version"),
                "evidence_ids": list(latest.get("evidence_ids", [])),
            })
            validation = {
                "status": latest.get("validation_status"),
                "errors": list(latest.get("validation_errors", [])),
                "confidence": snapshot.get("confidence"),
                "ambiguities": list(snapshot.get("ambiguities", [])),
            }

        context = None
        if manifest:
            context = {
                key: manifest.get(key)
                for key in ("policy_version", "mode", "total_token_budget", "actual_tokens", "provenance_coverage", "included_items", "pruned_items")
                if key in manifest
            }

        sql: dict[str, Any] = {"visible": False}
        if is_admin:
            audit = (
                await self.business_client.get_run_audit(run.run_id)
                if self.business_client is not None
                else None
            )
            sql = {"visible": True, "statement": None, "audit": None}
            if audit:
                sql = {
                    "visible": True,
                    "statement": audit.get("generatedSql"),
                    "audit": {
                        "event_type": audit.get("eventType"),
                        "success": audit.get("success"),
                    },
                }

        available = bool(latest or manifest)
        return {
            "available": available,
            "reason": None if available else "v2_evidence_not_available",
            "mode": self.mode,
            "run": {
                "id": run.run_id,
                "status": run.status.value,
                "parent_run_id": run.parent_run_id,
                "correlation_id": run.correlation_id,
                "operation_kind": run.operation_kind.value,
                "created_at": run.created_at.isoformat(),
                "completed_at": run.completed_at.isoformat() if run.completed_at else None,
            },
            "query_plan": query_plan,
            "assets": {
                "tables": [_candidate(item) for item in snapshot.get("table_candidates", [])],
                "columns": [_candidate(item) for item in snapshot.get("column_candidates", [])],
                "values": [_candidate(item) for item in snapshot.get("value_candidates", [])],
                "confirmed_joins": [
                    {
                        key: item[key]
                        for key in ("path_id", "relation_ids", "table_asset_ids", "score")
                        if key in item
                    }
                    for item in snapshot.get("join_paths", [])
                ],
            },
            "context": context,
            "validation": validation,
            "sql": sql,
        }
