from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from vanna.capabilities.agent_memory import AgentMemory
from vanna.capabilities.agent_memory.models import (
    TextMemory,
    TextMemorySearchResult,
    ToolMemory,
    ToolMemorySearchResult,
)


def _similarity(left: str, right: str) -> float:
    left_tokens = set(left.lower().split())
    right_tokens = set(right.lower().split())
    if not left_tokens or not right_tokens:
        return 0.0
    return len(left_tokens & right_tokens) / len(left_tokens | right_tokens)


class InMemoryAgentMemory(AgentMemory):
    """Small deterministic memory for the MVP; replace with pgvector later."""

    def __init__(self) -> None:
        self.tool_memories: list[ToolMemory] = []
        self.text_memories: list[TextMemory] = []

    async def save_tool_usage(
        self,
        question: str,
        tool_name: str,
        args: dict[str, Any],
        context,
        success: bool = True,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        self.tool_memories.append(
            ToolMemory(
                memory_id=str(uuid.uuid4()),
                question=question,
                tool_name=tool_name,
                args=args,
                timestamp=datetime.now(timezone.utc).isoformat(),
                success=success,
                metadata=metadata,
            )
        )

    async def save_text_memory(self, content: str, context) -> TextMemory:
        memory = TextMemory(
            memory_id=str(uuid.uuid4()),
            content=content,
            timestamp=datetime.now(timezone.utc).isoformat(),
        )
        self.text_memories.append(memory)
        return memory

    async def search_similar_usage(
        self,
        question: str,
        context,
        *,
        limit: int = 10,
        similarity_threshold: float = 0.7,
        tool_name_filter: str | None = None,
    ) -> list[ToolMemorySearchResult]:
        matches = []
        for memory in self.tool_memories:
            if tool_name_filter and memory.tool_name != tool_name_filter:
                continue
            score = _similarity(question, memory.question)
            if score >= similarity_threshold:
                matches.append((score, memory))
        matches.sort(key=lambda item: item[0], reverse=True)
        return [
            ToolMemorySearchResult(memory=memory, similarity_score=score, rank=index)
            for index, (score, memory) in enumerate(matches[:limit], 1)
        ]

    async def search_text_memories(
        self,
        query: str,
        context,
        *,
        limit: int = 10,
        similarity_threshold: float = 0.7,
    ) -> list[TextMemorySearchResult]:
        matches = [
            (_similarity(query, memory.content), memory) for memory in self.text_memories
        ]
        matches = [item for item in matches if item[0] >= similarity_threshold]
        matches.sort(key=lambda item: item[0], reverse=True)
        return [
            TextMemorySearchResult(memory=memory, similarity_score=score, rank=index)
            for index, (score, memory) in enumerate(matches[:limit], 1)
        ]

    async def get_recent_memories(self, context, limit: int = 10) -> list[ToolMemory]:
        return list(reversed(self.tool_memories[-limit:]))

    async def get_recent_text_memories(self, context, limit: int = 10) -> list[TextMemory]:
        return list(reversed(self.text_memories[-limit:]))

    async def delete_by_id(self, context, memory_id: str) -> bool:
        before = len(self.tool_memories)
        self.tool_memories = [m for m in self.tool_memories if m.memory_id != memory_id]
        return len(self.tool_memories) != before

    async def delete_text_memory(self, context, memory_id: str) -> bool:
        before = len(self.text_memories)
        self.text_memories = [m for m in self.text_memories if m.memory_id != memory_id]
        return len(self.text_memories) != before

    async def clear_memories(
        self, context, tool_name: str | None = None, before_date: str | None = None
    ) -> int:
        del before_date
        if tool_name:
            removed = sum(1 for m in self.tool_memories if m.tool_name == tool_name)
            self.tool_memories = [m for m in self.tool_memories if m.tool_name != tool_name]
            return removed
        removed = len(self.tool_memories) + len(self.text_memories)
        self.tool_memories.clear()
        self.text_memories.clear()
        return removed

