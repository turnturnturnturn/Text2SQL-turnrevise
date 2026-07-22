from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator
from typing import Any

from vanna.core.llm import LlmRequest, LlmResponse, LlmService, LlmStreamChunk


class NonBlockingLlmService(LlmService):
    """Keep synchronous third-party LLM adapters off the server event loop."""

    def __init__(self, delegate: LlmService) -> None:
        self.delegate = delegate

    @staticmethod
    def _run(coro):
        return asyncio.run(coro)

    async def send_request(self, request: LlmRequest) -> LlmResponse:
        return await asyncio.to_thread(self._run, self.delegate.send_request(request))

    async def stream_request(
        self, request: LlmRequest
    ) -> AsyncGenerator[LlmStreamChunk, None]:
        async def collect() -> list[LlmStreamChunk]:
            return [chunk async for chunk in self.delegate.stream_request(request)]

        chunks = await asyncio.to_thread(self._run, collect())
        for chunk in chunks:
            yield chunk

    async def validate_tools(self, tools: list[Any]) -> list[str]:
        return await asyncio.to_thread(self._run, self.delegate.validate_tools(tools))
