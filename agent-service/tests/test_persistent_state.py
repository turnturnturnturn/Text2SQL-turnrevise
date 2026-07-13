from types import SimpleNamespace

import pytest
from vanna.core.user import User

from app.state import (
    InMemoryStateRepository,
    MemoryService,
    PostgresAgentMemory,
    PostgresConversationStore,
    sanitize_memory_content,
)
from app.state.models import MemoryScope, MemoryStatus


def context(user_id: str):
    return SimpleNamespace(user=SimpleNamespace(id=user_id))


@pytest.mark.asyncio
async def test_conversation_store_is_user_scoped_and_delete_cascades_logically():
    repository = InMemoryStateRepository()
    store = PostgresConversationStore(repository)
    alice = User(id="00000000-0000-0000-0000-000000000001", username="alice")
    bob = User(id="00000000-0000-0000-0000-000000000002", username="bob")

    conversation = await store.create_conversation("conversation-1", alice, "销售额")
    assert (await store.get_conversation("conversation-1", alice)).messages[0].content == "销售额"
    assert await store.get_conversation("conversation-1", bob) is None
    assert await store.delete_conversation("conversation-1", bob) is False

    conversation.add_message(conversation.messages[0].model_copy(update={"role": "assistant", "content": "42"}))
    await store.update_conversation(conversation)
    assert len((await store.list_conversations(alice))[0].messages) == 2
    assert await store.delete_conversation("conversation-1", alice) is True
    assert await store.get_conversation("conversation-1", alice) is None


@pytest.mark.asyncio
async def test_conversation_update_cannot_take_over_another_users_id():
    repository = InMemoryStateRepository()
    store = PostgresConversationStore(repository)
    alice = User(id="00000000-0000-0000-0000-000000000001")
    bob = User(id="00000000-0000-0000-0000-000000000002")
    await store.create_conversation("same-id", alice, "hello")
    forged = await store.create_conversation("bob-id", bob, "hello")
    forged.id = "same-id"
    with pytest.raises(PermissionError, match="another user"):
        await store.update_conversation(forged)


@pytest.mark.asyncio
async def test_update_upserts_new_conversation_like_vanna_agent():
    repository = InMemoryStateRepository()
    store = PostgresConversationStore(repository)
    alice = User(id="00000000-0000-0000-0000-000000000001")
    from vanna.core.storage import Conversation, Message

    conversation = Conversation(
        id="new-from-agent", user=alice, messages=[Message(role="user", content="hello")]
    )
    await store.update_conversation(conversation)
    restored = await store.get_conversation("new-from-agent", alice)
    assert restored is not None
    assert restored.messages[0].content == "hello"


def test_memory_sanitizer_removes_pii_and_secrets():
    clean = sanitize_memory_content(
        "记住手机13812345678，邮箱 me@example.com，password=hunter2，"
        "token=eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxIn0.signature"
    )
    assert "13812345678" not in clean
    assert "me@example.com" not in clean
    assert "hunter2" not in clean
    assert "eyJhbGci" not in clean
    assert "[REDACTED_PHONE]" in clean
    assert "[REDACTED_EMAIL]" in clean


@pytest.mark.asyncio
async def test_structured_secret_values_and_memory_limit_are_enforced():
    repository = InMemoryStateRepository()
    service = MemoryService(repository, max_active_per_user=1)
    memory = await service.create_tool_candidate(
        "alice", "query", "tool",
        {"password": "hunter2", "phone": "13812345678"}, True,
    )
    assert memory.tool_args == {
        "password": "[REDACTED_SECRET]", "phone": "[REDACTED_PHONE]"
    }
    assert memory.expires_at is not None
    with pytest.raises(ValueError, match="memory limit"):
        await service.create_candidate("alice", "another memory")


@pytest.mark.asyncio
async def test_only_explicit_memory_language_creates_candidate():
    repository = InMemoryStateRepository()
    service = MemoryService(repository)
    user_id = "00000000-0000-0000-0000-000000000001"

    assert await service.extract_explicit_candidate(user_id, "帮我查销售额") is None
    memory = await service.extract_explicit_candidate(user_id, "请记住：默认按华东区域展示")
    assert memory is not None
    assert memory.status == MemoryStatus.CANDIDATE
    assert "华东" in memory.content


@pytest.mark.asyncio
async def test_candidate_is_not_recalled_until_confirmed_and_is_user_isolated():
    repository = InMemoryStateRepository()
    service = MemoryService(repository, embedder=lambda text: [1.0, float(len(text))])
    memory = await service.create_candidate("alice", "销售额默认按区域分组")

    assert await service.search_confirmed("alice", "销售额", similarity_threshold=0) == []
    assert await service.confirm("bob", memory.id) is None
    assert await service.search_confirmed("bob", "销售额", similarity_threshold=0) == []

    confirmed = await service.confirm("alice", memory.id)
    assert confirmed.status == MemoryStatus.CONFIRMED
    matches = await service.search_confirmed("alice", "销售额", similarity_threshold=0)
    assert [item[1].id for item in matches] == [memory.id]


@pytest.mark.asyncio
async def test_reject_and_delete_are_scoped_and_audited():
    repository = InMemoryStateRepository()
    service = MemoryService(repository)
    rejected = await service.create_candidate("alice", "以表格展示")
    deleted = await service.create_candidate("alice", "以万元展示")

    assert await service.reject("bob", rejected.id) is None
    assert (await service.reject("alice", rejected.id)).status == MemoryStatus.REJECTED
    assert await service.delete("bob", deleted.id) is False
    assert await service.delete("alice", deleted.id) is True
    assert await service.list_for_user("bob") == []
    assert [event.event_type for event in repository.memory_events] == [
        "created", "created", "rejected", "deleted"
    ]


@pytest.mark.asyncio
async def test_vanna_agent_memory_reads_only_confirmed_records():
    repository = InMemoryStateRepository()
    service = MemoryService(repository)
    agent_memory = PostgresAgentMemory(service)
    ctx = context("alice")

    await agent_memory.save_tool_usage(
        "查询华东销售额", "safe_read_sql", {"sql": "SELECT 1"}, ctx
    )
    assert await agent_memory.search_similar_usage(
        "查询华东销售额", ctx, similarity_threshold=0
    ) == []

    candidate = (await service.list_for_user("alice"))[0]
    await service.confirm("alice", candidate.id)
    matches = await agent_memory.search_similar_usage(
        "查询华东销售额", ctx, similarity_threshold=0
    )
    assert matches[0].memory.tool_name == "safe_read_sql"
    assert matches[0].memory.args == {"sql": "SELECT 1"}


@pytest.mark.asyncio
async def test_confirmed_global_business_memory_is_recalled_across_users():
    repository = InMemoryStateRepository()
    service = MemoryService(repository)
    global_memory = await service.extract_explicit_candidate(
        "admin", "全局记住：GMV 只统计已支付订单", allow_global=True
    )
    assert global_memory.scope == MemoryScope.GLOBAL
    await service.confirm("admin", global_memory.id)
    matches = await service.search_confirmed("alice", "GMV 已支付", similarity_threshold=0)
    assert [memory.id for _, memory in matches] == [global_memory.id]

    with pytest.raises(PermissionError, match="only admins"):
        await service.extract_explicit_candidate(
            "alice", "全局记住：退款额包括失败退款", allow_global=False
        )
