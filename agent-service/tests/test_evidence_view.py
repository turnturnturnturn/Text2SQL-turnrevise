import asyncio
import json

from app.evidence.service import EvidenceService
from app.evidence.artifact import build_evidence_artifact
from app.harness import RunRecord, RunStatus


class Plans:
    async def list_for_run(self, _run_id):
        return [{
            "sequence_no": 0,
            "plan_hash": "a" * 64,
            "plan_version": 2,
            "validation_status": "VALID",
            "validation_errors": [],
            "evidence_ids": ["metric:gmv", "column:paid_at"],
            "plan": {
                "metrics": [{"id": "metric:gmv", "alias": "gmv"}],
                "dimensions": [], "filters": [],
                "time_range": {"field_id": "column:paid_at", "preset": "LAST_30_DAYS"},
                "grain": [], "sort": [], "limit": 200, "join_path_ids": [],
                "evidence_ids": ["metric:gmv", "column:paid_at"], "ambiguities": [],
                "prompt": "must-not-leak",
            },
            "grounding_snapshot": {
                "tenant_id": "default", "confidence": 0.94, "ambiguities": [],
                "table_candidates": [{"asset_id": "table:orders", "source_hash": "b" * 64, "score": .9, "reasons": ["match"]}],
                "column_candidates": [{"asset_id": "column:paid_at", "source_hash": "c" * 64, "score": .8, "reasons": []}],
                "value_candidates": [], "join_paths": [], "knowledge_candidates": [],
                "evidence_ids": ["metric:gmv", "column:paid_at"],
                "query_hash": "d" * 64,
                "memory": "must-not-leak",
            },
        }]


class Context:
    async def get_manifest(self, _run_id):
        return {
            "policy_version": "context-v2", "mode": "shadow",
            "total_token_budget": 8192, "actual_tokens": 321,
            "provenance_coverage": 1.0,
            "included_items": [{"item_id": "i", "partition": "evidence", "source_id": "catalog:x", "source_hash": "e" * 64, "trust_level": "high", "token_count": 10, "mandatory": False}],
            "pruned_items": [{"item_id": "j", "partition": "memory", "reason": "budget", "estimated_tokens": 50}],
            "raw_prompt": "must-not-leak",
        }


class Audit:
    def __init__(self): self.calls = 0
    async def get_run_audit(self, run_id):
        self.calls += 1
        return {"runId": run_id, "generatedSql": "SELECT 1", "success": True, "eventType": "query"}


def test_evidence_view_is_structured_redacted_and_admin_sql_is_lazy():
    audit = Audit()
    service = EvidenceService(Plans(), Context(), audit, mode="shadow")
    run = RunRecord("11111111-1111-1111-1111-111111111111", "alice", "conv", "f" * 64, status=RunStatus.COMPLETED)

    owner = asyncio.run(service.build(run, is_admin=False))
    assert owner["run"]["status"] == "COMPLETED"
    assert owner["query_plan"]["metrics"][0]["id"] == "metric:gmv"
    assert owner["assets"]["tables"][0]["asset_id"] == "table:orders"
    assert owner["context"]["actual_tokens"] == 321
    assert owner["sql"] == {"visible": False}
    assert audit.calls == 0
    serialized = json.dumps(owner)
    for forbidden in ("must-not-leak", "raw_prompt", '"prompt"', '"memory":'):
        assert forbidden not in serialized

    admin = asyncio.run(service.build(run, is_admin=True))
    assert admin["sql"]["visible"] is True
    assert admin["sql"]["statement"] == "SELECT 1"
    assert audit.calls == 1


def test_evidence_view_has_explicit_empty_state():
    class Empty:
        async def list_for_run(self, _run_id): return []
        async def get_manifest(self, _run_id): return None

    view = asyncio.run(EvidenceService(Empty(), Empty(), None, mode="off").build(
        RunRecord("22222222-2222-2222-2222-222222222222", "alice", None, "a" * 64),
        is_admin=False,
    ))
    assert view["available"] is False
    assert view["reason"] == "v2_evidence_not_available"
    assert view["mode"] == "off"


def test_evidence_artifact_contains_only_run_id_and_lazy_url():
    component = build_evidence_artifact("run-123")
    payload = json.loads(component.rich_component.content)
    assert payload == {
        "run_id": "run-123",
        "evidence_url": "/api/runs/run-123/evidence",
    }
