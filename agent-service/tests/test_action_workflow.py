import pytest
from vanna import User

from app.workflow import ActionWorkflowHandler
from app.state import InMemoryStateRepository, MemoryService


class FakeBusinessClient:
    def __init__(self):
        self.confirmed = []
        self.cancelled = []

    async def confirm(self, action_id, token, user_id, role):
        self.confirmed.append((action_id, token, user_id, role))
        return {"message": "confirmed"}

    async def cancel(self, action_id, token, user_id, role):
        self.cancelled.append((action_id, token, user_id, role))
        return {"message": "cancelled"}


@pytest.mark.asyncio
async def test_explicit_confirmation_command_bypasses_llm():
    client = FakeBusinessClient()
    workflow = ActionWorkflowHandler(client)
    user = User(id="user-1", group_memberships=["operator"], metadata={"role": "operator"})
    result = await workflow.try_handle(
        None,
        user,
        None,
        "/confirm-action 00000000-0000-0000-0000-000000000001 abcdefghijklmnopqrstuvwxyz",
    )
    assert result.should_skip_llm is True
    assert len(client.confirmed) == 1


@pytest.mark.asyncio
async def test_normal_message_is_left_for_agent():
    workflow = ActionWorkflowHandler(FakeBusinessClient())
    user = User(id="user-1", group_memberships=["operator"], metadata={"role": "operator"})
    result = await workflow.try_handle(None, user, None, "把订单改成已发货")
    assert result.should_skip_llm is False


@pytest.mark.asyncio
async def test_explicit_memory_requires_deterministic_confirmation():
    service = MemoryService(InMemoryStateRepository())
    workflow = ActionWorkflowHandler(FakeBusinessClient(), service)
    user = User(id="user-1", group_memberships=["analyst"], metadata={"role": "analyst"})

    candidate = await workflow.try_handle(None, user, None, "请记住：销售额默认按区域分组")
    assert candidate.should_skip_llm is True
    memory = (await service.list_for_user("user-1"))[0]
    assert memory.status.value == "candidate"

    confirmed = await workflow.try_handle(
        None, user, None, f"/confirm-memory {memory.id}"
    )
    assert confirmed.should_skip_llm is True
    assert (await service.list_for_user("user-1", confirmed_only=True))[0].id == memory.id


@pytest.mark.asyncio
async def test_non_admin_global_memory_is_rejected_before_llm():
    service = MemoryService(InMemoryStateRepository())
    workflow = ActionWorkflowHandler(FakeBusinessClient(), service)
    user = User(id="user-1", group_memberships=["analyst"], metadata={"role": "analyst"})
    result = await workflow.try_handle(None, user, None, "全局记住：GMV包括草稿订单")
    assert result.should_skip_llm is True
    assert await service.list_for_user("user-1") == []
