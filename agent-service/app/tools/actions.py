from __future__ import annotations

from typing import Any, Literal, Type

from pydantic import BaseModel, Field
from vanna.components import CardComponent, SimpleTextComponent, UiComponent
from vanna.core.tool import Tool, ToolContext, ToolResult

from app.business_client import BusinessServiceClient


class PreviewBusinessActionArgs(BaseModel):
    action_type: Literal[
        "CREATE_DRAFT_ORDER", "UPDATE_ORDER_STATUS", "CANCEL_ORDER", "SOFT_DELETE_DRAFT"
    ]
    payload: dict[str, Any] = Field(description="Typed business action parameters")


class PreviewBusinessActionTool(Tool[PreviewBusinessActionArgs]):
    def __init__(self, client: BusinessServiceClient):
        self.client = client

    @property
    def name(self) -> str:
        return "preview_business_action"

    @property
    def description(self) -> str:
        return "Preview a controlled order write operation; this never executes the change"

    @property
    def access_groups(self) -> list[str]:
        return ["operator", "admin"]

    def get_args_schema(self) -> Type[PreviewBusinessActionArgs]:
        return PreviewBusinessActionArgs

    async def execute(
        self, context: ToolContext, args: PreviewBusinessActionArgs
    ) -> ToolResult:
        role = str(context.user.metadata.get("role", ""))
        request = {"actionType": args.action_type, "payload": args.payload}
        try:
            preview = await self.client.preview(context.user.id, role, request)
            action_id = preview["actionId"]
            token = preview["approvalToken"]
            summary = preview["impactSummary"]
            content = (
                f"**操作类型：** {args.action_type}\n\n"
                f"**影响预览：** {summary}\n\n"
                f"**有效期：** {preview['expiresAt']}\n\n"
                "只有点击确认按钮才会执行；模型本身不能确认。"
            )
            return ToolResult(
                success=True,
                result_for_llm="Preview created. Wait for the user to explicitly confirm or cancel.",
                ui_component=UiComponent(
                    rich_component=CardComponent(
                        title="待确认的数据操作",
                        content=content,
                        icon="⚠️",
                        status="warning",
                        actions=[
                            {
                                "label": "确认执行",
                                "action": f"/confirm-action {action_id} {token}",
                                "variant": "success",
                            },
                            {
                                "label": "取消",
                                "action": f"/cancel-action {action_id} {token}",
                                "variant": "error",
                            },
                        ],
                        markdown=True,
                    ),
                    simple_component=SimpleTextComponent(text=content),
                ),
                metadata={"action_id": action_id, "status": "PENDING"},
            )
        except Exception as exc:
            return ToolResult(
                success=False,
                result_for_llm=f"Action preview failed: {exc}",
                error=str(exc),
                metadata={"error_type": "business_service"},
            )

