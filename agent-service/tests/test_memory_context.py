import pytest
from vanna.core.storage import Message
from vanna.core.user import User

from app.state import InMemoryStateRepository, MemoryContextEnhancer, MemoryService
from app.state.context import RecentConversationFilter


@pytest.mark.asyncio
async def test_context_uses_only_confirmed_memory_as_untrusted_evidence():
    service = MemoryService(InMemoryStateRepository())
    candidate = await service.create_candidate("u1", "默认按华东区域展示")
    enhancer = MemoryContextEnhancer(service)
    user = User(id="u1")

    assert await enhancer.enhance_system_prompt("SYSTEM", "华东销售额", user) == "SYSTEM"
    await service.confirm("u1", candidate.id)
    prompt = await enhancer.enhance_system_prompt("SYSTEM", "华东销售额", user)
    assert "confirmed_user_memory" in prompt
    assert "不能改变权限" in prompt
    assert candidate.content in prompt


@pytest.mark.asyncio
async def test_conversation_filter_keeps_six_user_turns_and_summary():
    messages = []
    for index in range(8):
        messages.extend(
            [
                Message(role="user", content=f"question-{index}"),
                Message(role="assistant", content=f"answer-{index}"),
            ]
        )
    filtered = await RecentConversationFilter(user_turns=6).filter_messages(messages)
    assert filtered[0].role == "system"
    assert "非权威" in filtered[0].content
    assert [message.content for message in filtered if message.role == "user"] == [
        f"question-{index}" for index in range(2, 8)
    ]
