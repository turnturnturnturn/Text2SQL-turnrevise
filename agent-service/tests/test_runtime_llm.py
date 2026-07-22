import asyncio
import time

import pytest
from vanna.core.llm import LlmRequest, LlmResponse, LlmStreamChunk

from app.runtime.llm import NonBlockingLlmService


class BlockingDelegate:
    async def send_request(self, _request):
        time.sleep(0.15)
        return LlmResponse(content="ok")

    async def stream_request(self, _request):
        time.sleep(0.15)
        yield LlmStreamChunk(content="ok")

    async def validate_tools(self, _tools):
        time.sleep(0.15)
        return []


@pytest.mark.asyncio
async def test_sync_delegate_does_not_block_event_loop():
    service = NonBlockingLlmService(BlockingDelegate())
    request = LlmRequest(messages=[], user={"id": "test"})
    task = asyncio.create_task(service.send_request(request))

    await asyncio.sleep(0.01)
    assert task.done() is False
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


@pytest.mark.asyncio
async def test_stream_delegate_preserves_chunks():
    service = NonBlockingLlmService(BlockingDelegate())
    chunks = [
        chunk
        async for chunk in service.stream_request(
            LlmRequest(messages=[], user={"id": "test"})
        )
    ]
    assert [chunk.content for chunk in chunks] == ["ok"]
