from __future__ import annotations

import uuid
from collections.abc import AsyncGenerator

from vanna.servers.base import ChatHandler, ChatRequest, ChatStreamChunk

from app.runtime.correlation import bind_run_identity
from app.rollout.policy import bind_rollout_decision


class CorrelatedChatHandler(ChatHandler):
    """Make the server-issued UUID authoritative for SSE and durable run state."""

    def __init__(self, agent, rollout_service=None, rollout_policy_mode="shadow"):
        super().__init__(agent)
        self.rollout_service = rollout_service
        self.rollout_policy_mode = rollout_policy_mode

    async def handle_stream(
        self, request: ChatRequest
    ) -> AsyncGenerator[ChatStreamChunk, None]:
        conversation_id = request.conversation_id or self._generate_conversation_id()
        run_id = str(uuid.uuid4())
        with bind_run_identity(run_id, run_id):
            decision = None
            if self.rollout_policy_mode != "off" and self.rollout_service is not None:
                user = await self.agent.user_resolver.resolve_user(request.request_context)
                decision = await self.rollout_service.decide(
                    user_id=str(user.id), tenant_id="default",
                    role=str(user.metadata.get("role", "analyst")),
                    route="chat", risk="LOW",
                )
            manager = bind_rollout_decision(decision) if decision is not None else _null_context()
            with manager:
                async for component in self.agent.send_message(
                    request_context=request.request_context,
                    message=request.message,
                    conversation_id=conversation_id,
                ):
                    yield ChatStreamChunk.from_component(
                        component, conversation_id, run_id
                    )


class _null_context:
    def __enter__(self): return None
    def __exit__(self, *_args): return False
