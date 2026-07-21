from __future__ import annotations

import uuid
from collections.abc import AsyncGenerator

from vanna.servers.base import ChatHandler, ChatRequest, ChatStreamChunk

from app.runtime.correlation import bind_run_identity


class CorrelatedChatHandler(ChatHandler):
    """Make the server-issued UUID authoritative for SSE and durable run state."""

    async def handle_stream(
        self, request: ChatRequest
    ) -> AsyncGenerator[ChatStreamChunk, None]:
        conversation_id = request.conversation_id or self._generate_conversation_id()
        run_id = str(uuid.uuid4())
        with bind_run_identity(run_id, run_id):
            async for component in self.agent.send_message(
                request_context=request.request_context,
                message=request.message,
                conversation_id=conversation_id,
            ):
                yield ChatStreamChunk.from_component(
                    component, conversation_id, run_id
                )
