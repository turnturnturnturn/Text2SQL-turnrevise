from __future__ import annotations

from typing import Any

from vanna.capabilities.agent_memory import AgentMemory
from vanna.capabilities.agent_memory.models import (
    TextMemory,
    TextMemorySearchResult,
    ToolMemory,
    ToolMemorySearchResult,
)

from app.state.memory_service import MemoryService
from app.state.models import MemoryType


def _user_id(context) -> str:
    user_id = getattr(getattr(context, "user", None), "id", None)
    if not user_id:
        raise ValueError("memory operation requires an authenticated user")
    return str(user_id)


class PostgresAgentMemory(AgentMemory):
    """Vanna adapter. All writes are candidates; all reads are confirmed-only."""

    def __init__(self, service: MemoryService) -> None:
        self.service = service

    async def save_tool_usage(
        self, question: str, tool_name: str, args: dict[str, Any], context,
        success: bool = True, metadata: dict[str, Any] | None = None,
    ) -> None:
        await self.service.create_tool_candidate(
            _user_id(context), question, tool_name, args, success, metadata
        )

    async def save_text_memory(self, content: str, context) -> TextMemory:
        memory = await self.service.create_candidate(_user_id(context), content)
        return TextMemory(
            memory_id=memory.id, content=memory.content,
            timestamp=memory.created_at.isoformat(),
        )

    async def search_similar_usage(
        self, question: str, context, *, limit: int = 10,
        similarity_threshold: float = 0.7, tool_name_filter: str | None = None,
    ) -> list[ToolMemorySearchResult]:
        matches = await self.service.search_confirmed(
            _user_id(context), question, limit=limit,
            similarity_threshold=similarity_threshold,
            memory_type=MemoryType.TOOL_PATTERN, tool_name=tool_name_filter,
        )
        return [
            ToolMemorySearchResult(
                memory=ToolMemory(
                    memory_id=m.id, question=m.content, tool_name=m.tool_name or "",
                    args=m.tool_args or {}, timestamp=m.created_at.isoformat(),
                    success=True if m.success is None else m.success, metadata=m.metadata,
                ), similarity_score=score, rank=rank,
            )
            for rank, (score, m) in enumerate(matches, 1)
        ]

    async def search_text_memories(
        self, query: str, context, *, limit: int = 10,
        similarity_threshold: float = 0.7,
    ) -> list[TextMemorySearchResult]:
        matches = await self.service.search_confirmed(
            _user_id(context), query, limit=max(limit, 500),
            similarity_threshold=similarity_threshold,
        )
        matches = [(score, m) for score, m in matches if m.memory_type != MemoryType.TOOL_PATTERN]
        return [
            TextMemorySearchResult(
                memory=TextMemory(memory_id=m.id, content=m.content, timestamp=m.created_at.isoformat()),
                similarity_score=score, rank=rank,
            )
            for rank, (score, m) in enumerate(matches[:limit], 1)
        ]

    async def get_recent_memories(self, context, limit: int = 10) -> list[ToolMemory]:
        memories = await self.service.list_for_user(_user_id(context), confirmed_only=True)
        return [
            ToolMemory(memory_id=m.id, question=m.content, tool_name=m.tool_name or "",
                       args=m.tool_args or {}, timestamp=m.created_at.isoformat(),
                       success=True if m.success is None else m.success, metadata=m.metadata)
            for m in memories if m.memory_type == MemoryType.TOOL_PATTERN
        ][:limit]

    async def get_recent_text_memories(self, context, limit: int = 10) -> list[TextMemory]:
        memories = await self.service.list_for_user(_user_id(context), confirmed_only=True)
        return [TextMemory(memory_id=m.id, content=m.content, timestamp=m.created_at.isoformat())
                for m in memories if m.memory_type != MemoryType.TOOL_PATTERN][:limit]

    async def delete_by_id(self, context, memory_id: str) -> bool:
        return await self.service.delete(_user_id(context), memory_id)

    async def delete_text_memory(self, context, memory_id: str) -> bool:
        return await self.service.delete(_user_id(context), memory_id)

    async def clear_memories(
        self, context, tool_name: str | None = None, before_date: str | None = None
    ) -> int:
        user_id = _user_id(context)
        memories = await self.service.list_for_user(user_id)
        removed = 0
        for memory in memories:
            if tool_name and memory.tool_name != tool_name:
                continue
            if before_date and memory.created_at.isoformat() >= before_date:
                continue
            removed += int(await self.service.delete(user_id, memory.id))
        return removed
