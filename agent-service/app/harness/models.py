from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import StrEnum


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class RunStatus(StrEnum):
    RECEIVED = "RECEIVED"
    CONTEXT_READY = "CONTEXT_READY"
    MODEL_RUNNING = "MODEL_RUNNING"
    TOOL_RUNNING = "TOOL_RUNNING"
    VERIFYING = "VERIFYING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


TERMINAL_STATUSES = frozenset(
    {RunStatus.COMPLETED, RunStatus.FAILED, RunStatus.CANCELLED}
)

ALLOWED_TRANSITIONS: dict[RunStatus, frozenset[RunStatus]] = {
    RunStatus.RECEIVED: frozenset(
        {RunStatus.CONTEXT_READY, RunStatus.FAILED, RunStatus.CANCELLED}
    ),
    RunStatus.CONTEXT_READY: frozenset(
        {RunStatus.MODEL_RUNNING, RunStatus.FAILED, RunStatus.CANCELLED}
    ),
    RunStatus.MODEL_RUNNING: frozenset(
        {
            RunStatus.TOOL_RUNNING,
            RunStatus.VERIFYING,
            RunStatus.FAILED,
            RunStatus.CANCELLED,
        }
    ),
    RunStatus.TOOL_RUNNING: frozenset(
        {
            RunStatus.MODEL_RUNNING,
            RunStatus.VERIFYING,
            RunStatus.FAILED,
            RunStatus.CANCELLED,
        }
    ),
    RunStatus.VERIFYING: frozenset(
        {RunStatus.COMPLETED, RunStatus.FAILED, RunStatus.CANCELLED}
    ),
    RunStatus.COMPLETED: frozenset(),
    RunStatus.FAILED: frozenset(),
    RunStatus.CANCELLED: frozenset(),
}


class InvalidRunTransition(ValueError):
    pass


class HarnessBudgetExceeded(RuntimeError):
    pass


@dataclass(frozen=True)
class HarnessBudget:
    timeout_seconds: float = 120.0
    max_tool_calls: int = 8
    max_read_retries: int = 2

    def __post_init__(self) -> None:
        if self.timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        if self.max_tool_calls < 1:
            raise ValueError("max_tool_calls must be at least 1")
        if self.max_read_retries < 0:
            raise ValueError("max_read_retries cannot be negative")


@dataclass
class RunRecord:
    run_id: str
    user_id: str
    conversation_id: str | None
    instruction_hash: str
    status: RunStatus = RunStatus.RECEIVED
    failure_type: str | None = None
    tool_call_count: int = 0
    retry_count: int = 0
    created_at: datetime = field(default_factory=utc_now)
    updated_at: datetime = field(default_factory=utc_now)
    completed_at: datetime | None = None


@dataclass(frozen=True)
class RunStep:
    run_id: str
    status: RunStatus
    occurred_at: datetime = field(default_factory=utc_now)
    failure_type: str | None = None
