import pytest
from vanna import User

from app.workflow import ActionWorkflowHandler


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

