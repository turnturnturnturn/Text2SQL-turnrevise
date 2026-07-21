from datetime import datetime, timedelta, timezone

import jwt
from fastapi import FastAPI
from fastapi.testclient import TestClient
from vanna.core import RichTextComponent, SimpleTextComponent, UiComponent

from app.harness import InMemoryRunStore, RunRecord, RunStatus
from app.harness.clarification import (
    ClarificationOption,
    ClarificationService,
    InMemoryClarificationStore,
    MaterialAmbiguity,
)
from app.security.jwt_resolver import JwtUserResolver
from app.state import InMemoryStateRepository, MemoryService
from app.state.api import create_state_router
from app.context_v2 import ContextCompiler, ContextItem, ContextPartition
from app.context_v2.store import InMemoryContextStore
from app.observability.trace import InMemoryTraceStore, TraceService


SECRET = "state-api-test-secret-that-is-at-least-32-characters"


class FakeConversationStore:
    async def delete_conversation(self, conversation_id, user):
        return conversation_id == "owned" and user.id == "alice"

    async def find_instruction_by_hash(self, conversation_id, user_id, instruction_hash):
        if conversation_id == "conversation" and user_id == "alice":
            return "original question"
        return None


def token(user_id: str, role: str = "analyst") -> str:
    return jwt.encode(
        {
            "sub": user_id,
            "role": role,
            "exp": datetime.now(timezone.utc) + timedelta(minutes=5),
        },
        SECRET,
        algorithm="HS256",
    )


def test_state_api_is_authenticated_and_user_scoped():
    repository = InMemoryStateRepository()
    service = MemoryService(repository)
    run_store = InMemoryRunStore()
    clarification_service = ClarificationService(
        run_store, InMemoryClarificationStore()
    )
    context_store = InMemoryContextStore()
    trace_service = TraceService(InMemoryTraceStore())

    class FakeQueryPlanStore:
        async def list_for_run(self, run_id):
            return [{"plan_hash": "a" * 64, "evidence_ids": ["metric:gmv"]}]

    app = FastAPI()

    async def resume_handler(child, instruction, selection, authorization):
        assert instruction == "original question"
        assert selection["evidence_id"] == "column:paid_at"
        yield UiComponent(
            rich_component=RichTextComponent(content="resumed"),
            simple_component=SimpleTextComponent(text="resumed"),
        )

    app.include_router(
        create_state_router(
            resolver=JwtUserResolver(SECRET),
            memory_service=service,
            conversation_store=FakeConversationStore(),
            run_store=run_store,
            query_plan_store=FakeQueryPlanStore(),
            clarification_service=clarification_service,
            context_store=context_store,
            clarification_resume_mode="shadow",
            resume_handler=resume_handler,
            trace_service=trace_service,
        )
    )

    import asyncio

    alice_memory = asyncio.run(service.create_candidate("alice", "默认按区域展示"))
    asyncio.run(service.create_candidate("bob", "不应泄露"))
    asyncio.run(run_store.create(RunRecord("run-alice", "alice", None, "a" * 64)))
    asyncio.run(trace_service.append("run-alice", "request_received", {"tenant_id": "default"}))
    manifest = ContextCompiler(token_estimator=len).compile(
        run_id="run-alice",
        total_token_budget=100,
        mode="shadow",
        items=[
            ContextItem(
                item_id="secret-item",
                partition=ContextPartition.REQUEST,
                content="do not return this raw request",
                source_id="request:run-alice",
                source_hash="e" * 64,
                trust_level="current_request",
                mandatory=True,
            )
        ],
    ).manifest
    asyncio.run(context_store.save_manifest(manifest))
    asyncio.run(
        run_store.create(RunRecord("clarify-alice", "alice", "conversation", "b" * 64))
    )
    asyncio.run(run_store.transition("clarify-alice", RunStatus.CONTEXT_BUILDING))
    asyncio.run(run_store.transition("clarify-alice", RunStatus.LINKING))
    asyncio.run(
        clarification_service.create(
            parent_run_id="clarify-alice",
            user_id="alice",
            ambiguity=MaterialAmbiguity(
                ambiguity_id="a1",
                question="paid_at or created_at?",
                options=(
                    ClarificationOption(
                        "paid", "Paid time", "column:paid_at", "column:paid_at", "c" * 64
                    ),
                    ClarificationOption(
                        "created", "Created time", "column:created_at", "column:created_at", "d" * 64
                    ),
                ),
                reason="two candidates",
            ),
        )
    )
    client = TestClient(app)

    assert client.get("/api/memories").status_code == 401
    headers = {"Authorization": f"Bearer {token('alice')}"}
    listed = client.get("/api/memories", headers=headers)
    assert listed.status_code == 200
    assert [item["id"] for item in listed.json()] == [alice_memory.id]

    confirmed = client.post(
        f"/api/memories/{alice_memory.id}/confirm", headers=headers
    )
    assert confirmed.status_code == 200
    assert confirmed.json()["status"] == "confirmed"
    assert client.get("/api/runs/run-alice", headers=headers).status_code == 200
    evidence = client.get("/api/runs/run-alice/evidence", headers=headers)
    assert evidence.status_code == 200
    assert evidence.json()["query_plans"][0]["evidence_ids"] == ["metric:gmv"]
    assert client.get(
        "/api/runs/run-alice",
        headers={"Authorization": f"Bearer {token('bob')}"},
    ).status_code == 404
    assert client.get(
        "/api/runs/run-alice/evidence",
        headers={"Authorization": f"Bearer {token('bob')}"},
    ).status_code == 404
    assert client.get(
        "/api/runs/run-alice/evidence",
        headers={"Authorization": f"Bearer {token('admin-user', 'admin')}"},
    ).status_code == 200
    assert client.get("/api/runs/run-alice/trace", headers=headers).json()["events"][0]["event_type"] == "request_received"
    assert client.get(
        "/api/runs/run-alice/trace",
        headers={"Authorization": f"Bearer {token('bob')}"},
    ).status_code == 404
    assert client.get(
        "/api/runs/run-alice/trace",
        headers={"Authorization": f"Bearer {token('admin-user', 'admin')}"},
    ).status_code == 200
    context_manifest = client.get(
        "/api/runs/run-alice/context-manifest", headers=headers
    )
    assert context_manifest.status_code == 200
    assert "do not return this raw request" not in context_manifest.text
    assert context_manifest.json()["provenance_coverage"] == 1.0
    assert client.get(
        "/api/runs/run-alice/context-manifest",
        headers={"Authorization": f"Bearer {token('bob')}"},
    ).status_code == 404
    assert client.delete("/api/conversations/owned", headers=headers).status_code == 204
    clarified = client.post(
        "/api/runs/clarify-alice/clarify",
        headers=headers,
        json={"option_id": "paid"},
    )
    assert clarified.status_code == 200
    assert clarified.json()["parent_run_id"] == "clarify-alice"
    assert clarified.json()["resume_token"]
    assert client.post(
        "/api/runs/clarify-alice/clarify",
        headers={"Authorization": f"Bearer {token('bob')}"},
        json={"option_id": "paid"},
    ).status_code == 404
    resumed = client.post(
        f"/api/runs/{clarified.json()['child_run_id']}/resume",
        headers=headers,
        json={"token": clarified.json()["resume_token"]},
    )
    assert resumed.status_code == 200
    assert "resumed" in resumed.text
    assert client.post(
        f"/api/runs/{clarified.json()['child_run_id']}/resume",
        headers=headers,
        json={"token": clarified.json()["resume_token"]},
    ).status_code == 409
    assert client.post(
        "/api/runs/run-alice/cancel", headers=headers
    ).status_code == 200
    assert client.post(
        "/api/runs/run-alice/cancel", headers=headers
    ).json()["status"] == "CANCELLED"
