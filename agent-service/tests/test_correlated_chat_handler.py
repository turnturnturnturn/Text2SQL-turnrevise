import uuid

import pytest
from vanna.core import RichTextComponent, SimpleTextComponent, UiComponent
from vanna.servers.base import ChatRequest

from app.runtime.correlation import current_run_id
from app.runtime.chat_handler import CorrelatedChatHandler


class CapturingAgent:
    def __init__(self):
        self.run_id = None

    async def send_message(self, **_kwargs):
        self.run_id = current_run_id()
        yield UiComponent(
            rich_component=RichTextComponent(content="ok"),
            simple_component=SimpleTextComponent(text="ok"),
        )


@pytest.mark.asyncio
async def test_correlated_handler_ignores_client_request_id_for_durable_identity():
    agent = CapturingAgent()
    handler = CorrelatedChatHandler(agent)
    chunks = [
        chunk
        async for chunk in handler.handle_stream(
            ChatRequest(message="question", request_id="client-controlled")
        )
    ]

    assert len(chunks) == 1
    assert chunks[0].request_id == agent.run_id
    assert chunks[0].request_id != "client-controlled"
    uuid.UUID(chunks[0].request_id)
    assert current_run_id() is None
