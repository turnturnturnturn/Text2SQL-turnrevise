from __future__ import annotations

import re
from typing import TYPE_CHECKING

from vanna.components import CardComponent, SimpleTextComponent, UiComponent
from vanna.core.workflow import WorkflowHandler, WorkflowResult

from app.business_client import BusinessServiceClient

if TYPE_CHECKING:
    from app.state.memory_service import MemoryService


ACTION_PATTERN = re.compile(
    r"^/(?P<verb>confirm-action|cancel-action) "
    r"(?P<action_id>[0-9a-fA-F-]{36}) (?P<token>[A-Za-z0-9_-]{20,})$"
)
MEMORY_PATTERN = re.compile(
    r"^/(?P<verb>confirm-memory|reject-memory) (?P<memory_id>[0-9a-fA-F-]{36})$"
)


class ActionWorkflowHandler(WorkflowHandler):
    def __init__(self, client: BusinessServiceClient, memory_service: "MemoryService | None" = None):
        self.client = client
        self.memory_service = memory_service

    async def try_handle(self, agent, user, conversation, message: str) -> WorkflowResult:
        memory_match = MEMORY_PATTERN.fullmatch(message.strip())
        if memory_match and self.memory_service is not None:
            method = (
                self.memory_service.confirm
                if memory_match["verb"] == "confirm-memory"
                else self.memory_service.reject
            )
            memory = await method(str(user.id), memory_match["memory_id"])
            if memory is None:
                return self._card("记忆未更新", "找不到该候选记忆，或它不属于当前用户。", "error", "❌")
            confirmed = memory_match["verb"] == "confirm-memory"
            return self._card(
                "记忆已确认" if confirmed else "记忆已拒绝",
                memory.content,
                "success" if confirmed else "info",
                "🧠" if confirmed else "🗑️",
            )

        match = ACTION_PATTERN.fullmatch(message.strip())
        if not match:
            if self.memory_service is not None:
                try:
                    candidate = await self.memory_service.extract_explicit_candidate(
                        str(user.id),
                        message,
                        allow_global=str(user.metadata.get("role", "")) == "admin",
                    )
                except PermissionError as exc:
                    return self._card("无权创建全局记忆", str(exc), "error", "🔒")
                if candidate is not None:
                    content = (
                        f"候选内容：{candidate.content}\n\n"
                        "只有确认后，它才会用于后续对话。"
                    )
                    return WorkflowResult(
                        should_skip_llm=True,
                        components=[
                            UiComponent(
                                rich_component=CardComponent(
                                    title="是否保存为长期记忆？",
                                    content=content,
                                    icon="🧠",
                                    status="warning",
                                    actions=[
                                        {
                                            "label": "确认记忆",
                                            "action": f"/confirm-memory {candidate.id}",
                                            "variant": "success",
                                        },
                                        {
                                            "label": "拒绝",
                                            "action": f"/reject-memory {candidate.id}",
                                            "variant": "error",
                                        },
                                    ],
                                    markdown=True,
                                ),
                                simple_component=SimpleTextComponent(text=content),
                            )
                        ],
                    )
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

    @staticmethod
    def _card(title: str, text: str, status: str, icon: str) -> WorkflowResult:
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
