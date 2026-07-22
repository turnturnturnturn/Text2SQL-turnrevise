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


class DisconnectAwareAgent:
    def __init__(self):
        self.closed = False

    async def send_message(self, **_kwargs):
        try:
            while True:
                yield UiComponent(
                    rich_component=RichTextComponent(content="streaming"),
                    simple_component=SimpleTextComponent(text="streaming"),
                )
        finally:
            self.closed = True


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


@pytest.mark.asyncio
async def test_correlated_handler_closes_agent_stream_when_client_disconnects():
    agent = DisconnectAwareAgent()
    handler = CorrelatedChatHandler(agent)
    stream = handler.handle_stream(ChatRequest(message="question"))

    await anext(stream)
    await stream.aclose()

    assert agent.closed is True
    assert current_run_id() is None
