from __future__ import annotations

import math
import re
import uuid
import hashlib
from collections.abc import Callable, Sequence
from datetime import datetime, timedelta, timezone
from typing import Any

from app.state.models import (
    MemoryEvent,
    MemoryRecord,
    MemoryScope,
    MemoryStatus,
    MemoryType,
    MemoryValidity,
)
from app.state.repositories import StateRepository

Embedder = Callable[[str], Sequence[float]]

_EXPLICIT_MEMORY = re.compile(
    r"(?:请)?(?:记住|记一下|以后(?:都)?按|之后(?:都)?按|默认按)\s*[:：,，]?\s*(.+)",
    re.IGNORECASE | re.DOTALL,
)
_GLOBAL_MEMORY = re.compile(
    r"(?:请)?全局记住\s*[:：,，]?\s*(.+)", re.IGNORECASE | re.DOTALL
)
_EMAIL = re.compile(r"(?<![\w.-])[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}(?![\w.-])")
_PHONE = re.compile(r"(?<!\d)(?:\+?86[- ]?)?1[3-9]\d{9}(?!\d)")
_JWT = re.compile(r"\beyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\b")
_SECRET = re.compile(
    r"(?i)\b(api[_-]?key|token|password|passwd|secret)\b\s*[:=]\s*([^\s,;，；]+)"
)


def sanitize_memory_content(content: str) -> str:
    """Remove common PII and credentials before any persistence or embedding."""
    value = _JWT.sub("[REDACTED_TOKEN]", content)
    value = _EMAIL.sub("[REDACTED_EMAIL]", value)
    value = _PHONE.sub("[REDACTED_PHONE]", value)
    value = _SECRET.sub(lambda match: f"{match.group(1)}=[REDACTED_SECRET]", value)
    return " ".join(value.split()).strip()


def normalize_memory_content(content: str) -> str:
    """Return the stable normalized form used for memory deduplication."""
    return re.sub(r"[\W_]+", "", content, flags=re.UNICODE).lower()


def _tokens(content: str) -> set[str]:
    compact = normalize_memory_content(content)
    words = set(re.findall(r"[a-z0-9]+|[\u4e00-\u9fff]", content.lower()))
    words.update(
        compact[index : index + 2] for index in range(max(0, len(compact) - 1))
    )
    return {token for token in words if token}


def _keyword_similarity(left: str, right: str) -> float:
    a, b = _tokens(left), _tokens(right)
    return len(a & b) / len(a | b) if a and b else 0.0


def _cosine(left: Sequence[float] | None, right: Sequence[float] | None) -> float:
    if not left or not right or len(left) != len(right):
        return 0.0
    denominator = math.sqrt(sum(x * x for x in left)) * math.sqrt(
        sum(x * x for x in right)
    )
    return sum(x * y for x, y in zip(left, right)) / denominator if denominator else 0.0


def _sanitize_value(value: Any) -> Any:
    if isinstance(value, str):
        return sanitize_memory_content(value)
    if isinstance(value, dict):
        sanitized = {}
        for key, item in value.items():
            key_text = str(key)
            sanitized[key_text] = (
                "[REDACTED_SECRET]"
                if re.search(
                    r"(?i)(?:password|passwd|secret|token|api[_-]?key)", key_text
                )
                else _sanitize_value(item)
            )
        return sanitized
    if isinstance(value, list):
        return [_sanitize_value(item) for item in value]
    return value


class MemoryService:
    def __init__(
        self,
        repository: StateRepository,
        embedder: Embedder | None = None,
        *,
        max_active_per_user: int = 500,
        retention_days: int = 90,
    ) -> None:
        self.repository = repository
        self.embedder = embedder
        self.max_active_per_user = max_active_per_user
        self.retention_days = retention_days

    def _embedding(self, content: str) -> list[float] | None:
        return list(map(float, self.embedder(content))) if self.embedder else None

    async def create_candidate(
        self,
        user_id: str,
        content: str,
        memory_type: MemoryType = MemoryType.USER_PREFERENCE,
        *,
        source: str = "explicit_instruction",
        tool_name: str | None = None,
        tool_args: dict[str, Any] | None = None,
        success: bool | None = None,
        metadata: dict[str, Any] | None = None,
        scope: MemoryScope = MemoryScope.USER,
        validity: MemoryValidity = MemoryValidity.ACTIVE,
        source_hash: str | None = None,
        conflict_key: str | None = None,
        valid_from: datetime | None = None,
        valid_to: datetime | None = None,
    ) -> MemoryRecord:
        clean = sanitize_memory_content(content)
        if not clean:
            raise ValueError("memory content is empty after sanitization")
        normalized = normalize_memory_content(clean)
        existing = await self.repository.list_memories(user_id)
        duplicate = next(
            (
                item
                for item in existing
                if item.memory_type == memory_type
                and item.normalized_content == normalized
            ),
            None,
        )
        active_count = sum(item.status != MemoryStatus.REJECTED for item in existing)
        if duplicate is None and active_count >= self.max_active_per_user:
            raise ValueError("active memory limit reached for user")
        record = MemoryRecord(
            id=str(uuid.uuid4()),
            user_id=user_id,
            memory_type=memory_type,
            scope=scope,
            status=MemoryStatus.CANDIDATE,
            content=clean,
            normalized_content=normalized,
            source=source,
            tool_name=tool_name,
            tool_args=_sanitize_value(tool_args),
            success=success,
            embedding=self._embedding(clean),
            metadata=_sanitize_value(metadata or {}),
            expires_at=datetime.now(timezone.utc) + timedelta(days=self.retention_days),
            validity=validity,
            source_hash=source_hash
            or hashlib.sha256(clean.encode("utf-8")).hexdigest(),
            conflict_key=conflict_key,
            valid_from=valid_from,
            valid_to=valid_to,
        )
        stored = await self.repository.upsert_memory(record)
        await self.repository.add_memory_event(
            MemoryEvent(stored.id, user_id, "created", {"source": source})
        )
        return stored

    async def extract_explicit_candidate(
        self, user_id: str, instruction: str, *, allow_global: bool = False
    ) -> MemoryRecord | None:
        global_match = _GLOBAL_MEMORY.search(instruction)
        if global_match:
            if not allow_global:
                raise PermissionError("only admins can create global memory candidates")
            return await self.create_candidate(
                user_id,
                global_match.group(1),
                MemoryType.BUSINESS_TERM,
                scope=MemoryScope.GLOBAL,
            )
        match = _EXPLICIT_MEMORY.search(instruction)
        if not match:
            return None
        return await self.create_candidate(user_id, match.group(1))

    async def confirm(self, user_id: str, memory_id: str) -> MemoryRecord | None:
        atomic_confirm = getattr(self.repository, "confirm_memory_atomic", None)
        if atomic_confirm is not None:
            return await atomic_confirm(memory_id, user_id)
        memory = await self.repository.set_memory_status(
            memory_id, user_id, MemoryStatus.CONFIRMED
        )
        if memory:
            await self.repository.add_memory_event(
                MemoryEvent(memory.id, user_id, "confirmed")
            )
            if memory.validity == MemoryValidity.ACTIVE and memory.conflict_key:
                existing = await self.repository.list_memories(
                    user_id, {MemoryStatus.CONFIRMED}
                )
                for other in existing:
                    if (
                        other.id != memory.id
                        and other.conflict_key == memory.conflict_key
                        and other.validity == MemoryValidity.ACTIVE
                    ):
                        await self.repository.set_memory_validity(
                            other.id, other.user_id, MemoryValidity.SUPERSEDED
                        )
                        await self.repository.add_memory_event(
                            MemoryEvent(
                                other.id,
                                other.user_id,
                                "superseded",
                                {"superseded_by": memory.id},
                            )
                        )
        return memory

    async def reject(self, user_id: str, memory_id: str) -> MemoryRecord | None:
        memory = await self.repository.set_memory_status(
            memory_id, user_id, MemoryStatus.REJECTED
        )
        if memory:
            await self.repository.add_memory_event(
                MemoryEvent(memory.id, user_id, "rejected")
            )
        return memory

    async def delete(self, user_id: str, memory_id: str) -> bool:
        memory = await self.repository.get_memory(memory_id, user_id)
        if not memory:
            return False
        await self.repository.add_memory_event(
            MemoryEvent(memory.id, user_id, "deleted")
        )
        return await self.repository.delete_memory(memory_id, user_id)

    async def list_for_user(
        self, user_id: str, *, confirmed_only: bool = False
    ) -> list[MemoryRecord]:
        statuses = {MemoryStatus.CONFIRMED} if confirmed_only else None
        memories = await self.repository.list_memories(user_id, statuses)
        if not confirmed_only:
            return memories
        now = datetime.now(timezone.utc)
        return [
            memory
            for memory in memories
            if memory.validity == MemoryValidity.ACTIVE
            and (memory.valid_from is None or memory.valid_from <= now)
            and (memory.valid_to is None or memory.valid_to > now)
        ]

    async def search_confirmed(
        self,
        user_id: str,
        query: str,
        *,
        limit: int = 5,
        similarity_threshold: float = 0.0,
        memory_type: MemoryType | None = None,
        tool_name: str | None = None,
    ) -> list[tuple[float, MemoryRecord]]:
        clean_query = sanitize_memory_content(query)
        query_embedding = self._embedding(clean_query)
        memories = await self.repository.list_memories(
            user_id, {MemoryStatus.CONFIRMED}, include_global=True
        )
        candidates: list[tuple[MemoryRecord, float, float]] = []
        for memory in memories:
            now = datetime.now(timezone.utc)
            if memory.validity != MemoryValidity.ACTIVE:
                continue
            if memory.valid_from and memory.valid_from > now:
                continue
            if memory.valid_to and memory.valid_to <= now:
                continue
            if memory_type and memory.memory_type != memory_type:
                continue
            if tool_name and memory.tool_name != tool_name:
                continue
            keyword = _keyword_similarity(clean_query, memory.content)
            vector = _cosine(query_embedding, memory.embedding)
            candidates.append((memory, keyword, vector))

        keyword_rank = {
            item[0].id: rank
            for rank, item in enumerate(
                sorted(
                    candidates,
                    key=lambda item: (item[1], item[0].updated_at),
                    reverse=True,
                ),
                1,
            )
        }
        vector_rank = {
            item[0].id: rank
            for rank, item in enumerate(
                sorted(
                    candidates,
                    key=lambda item: (item[2], item[0].updated_at),
                    reverse=True,
                ),
                1,
            )
            if query_embedding is not None
        }
        ranked: list[tuple[float, float, MemoryRecord]] = []
        for memory, keyword, vector in candidates:
            similarity = max(
                keyword, vector, (keyword + vector) / 2 if vector else keyword
            )
            if similarity < similarity_threshold:
                continue
            rrf = 1 / (60 + keyword_rank[memory.id])
            if memory.id in vector_rank:
                rrf += 1 / (60 + vector_rank[memory.id])
            ranked.append((rrf, similarity, memory))
        ranked.sort(
            key=lambda item: (item[0], item[1], item[2].updated_at), reverse=True
        )
        return [
            (similarity, memory) for _, similarity, memory in ranked[: max(0, limit)]
        ]

    async def create_tool_candidate(
        self,
        user_id: str,
        question: str,
        tool_name: str,
        args: dict[str, Any],
        success: bool,
        metadata: dict[str, Any] | None = None,
    ) -> MemoryRecord:
        return await self.create_candidate(
            user_id,
            sanitize_memory_content(question),
            MemoryType.TOOL_PATTERN,
            source="tool_usage",
            tool_name=tool_name,
            tool_args=args,
            success=success,
            metadata=metadata,
        )
