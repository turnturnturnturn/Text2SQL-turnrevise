from datetime import datetime, timedelta, timezone

import jwt
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.harness import InMemoryRunStore, RunRecord
from app.security.jwt_resolver import JwtUserResolver
from app.state import InMemoryStateRepository, MemoryService
from app.state.api import create_state_router


SECRET = "state-api-test-secret-that-is-at-least-32-characters"


class FakeConversationStore:
    async def delete_conversation(self, conversation_id, user):
        return conversation_id == "owned" and user.id == "alice"


def token(user_id: str) -> str:
    return jwt.encode(
        {
            "sub": user_id,
            "role": "analyst",
            "exp": datetime.now(timezone.utc) + timedelta(minutes=5),
        },
        SECRET,
        algorithm="HS256",
    )


def test_state_api_is_authenticated_and_user_scoped():
    repository = InMemoryStateRepository()
    service = MemoryService(repository)
    run_store = InMemoryRunStore()
    app = FastAPI()
    app.include_router(
        create_state_router(
            resolver=JwtUserResolver(SECRET),
            memory_service=service,
            conversation_store=FakeConversationStore(),
            run_store=run_store,
        )
    )

    import asyncio

    alice_memory = asyncio.run(service.create_candidate("alice", "默认按区域展示"))
    asyncio.run(service.create_candidate("bob", "不应泄露"))
    asyncio.run(run_store.create(RunRecord("run-alice", "alice", None, "a" * 64)))
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
    assert client.get(
        "/api/runs/run-alice",
        headers={"Authorization": f"Bearer {token('bob')}"},
    ).status_code == 404
    assert client.delete("/api/conversations/owned", headers=headers).status_code == 204
