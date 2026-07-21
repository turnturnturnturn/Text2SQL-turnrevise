from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import StrEnum
from typing import Any


class MemoryStatus(StrEnum):
    CANDIDATE = "candidate"
    CONFIRMED = "confirmed"
    REJECTED = "rejected"


class MemoryType(StrEnum):
    USER_PREFERENCE = "USER_PREFERENCE"
    BUSINESS_TERM = "BUSINESS_TERM"
    VERIFIED_QUERY = "VERIFIED_QUERY"
    TOOL_PATTERN = "TOOL_PATTERN"


class MemoryScope(StrEnum):
    USER = "USER"
    GLOBAL = "GLOBAL"


class MemoryValidity(StrEnum):
    ACTIVE = "ACTIVE"
    CONFLICTED = "CONFLICTED"
    SUPERSEDED = "SUPERSEDED"
    INVALID = "INVALID"


@dataclass(slots=True)
class MemoryRecord:
    id: str
    user_id: str
    memory_type: MemoryType
    status: MemoryStatus
    content: str
    normalized_content: str
    source: str
    scope: MemoryScope = MemoryScope.USER
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    tool_name: str | None = None
    tool_args: dict[str, Any] | None = None
    success: bool | None = None
    embedding: list[float] | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    expires_at: datetime | None = None
    validity: MemoryValidity = MemoryValidity.ACTIVE
    source_hash: str | None = None
    conflict_key: str | None = None
    valid_from: datetime | None = None
    valid_to: datetime | None = None


@dataclass(slots=True)
class MemoryEvent:
    memory_id: str
    user_id: str
    event_type: str
    details: dict[str, Any] = field(default_factory=dict)
