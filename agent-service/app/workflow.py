from __future__ import annotations

import re

from vanna.components import CardComponent, SimpleTextComponent, UiComponent
from vanna.core.workflow import WorkflowHandler, WorkflowResult

from app.business_client import BusinessServiceClient


ACTION_PATTERN = re.compile(
    r"^/(?P<verb>confirm-action|cancel-action) "
    r"(?P<action_id>[0-9a-fA-F-]{36}) (?P<token>[A-Za-z0-9_-]{20,})$"
)


class ActionWorkflowHandler(WorkflowHandler):
    def __init__(self, client: BusinessServiceClient):
        self.client = client

    async def try_handle(self, agent, user, conversation, message: str) -> WorkflowResult:
        match = ACTION_PATTERN.fullmatch(message.strip())
        if not match:
            return WorkflowResult(should_skip_llm=False)

        role = str(user.metadata.get("role", ""))
        try:
            if match["verb"] == "confirm-action":
                result = await self.client.confirm(
                    match["action_id"], match["token"], user.id, role
                )
                title = "操作已执行"
                status = "success"
                icon = "✅"
            else:
                result = await self.client.cancel(
                    match["action_id"], match["token"], user.id, role
                )
                title = "操作已取消"
                status = "info"
                icon = "🛑"
            text = str(result.get("message", result))
        except Exception as exc:
            title = "操作未执行"
            status = "error"
            icon = "❌"
            text = str(exc)

        return WorkflowResult(
            should_skip_llm=True,
            components=[
                UiComponent(
                    rich_component=CardComponent(
                        title=title,
                        content=text,
                        icon=icon,
                        status=status,
                        markdown=True,
                    ),
                    simple_component=SimpleTextComponent(text=text),
                )
            ],
        )

    async def get_starter_ui(self, agent, user, conversation):
        role = user.metadata.get("role", "unknown")
        text = f"当前角色：**{role}**。可以查询订单、销售额、退款率和商品排行。"
        return [
            UiComponent(
                rich_component=CardComponent(
                    title="Enterprise Database Copilot",
                    content=text,
                    icon="🗄️",
                    status="success",
                    markdown=True,
                ),
                simple_component=SimpleTextComponent(text=text),
            )
        ]

