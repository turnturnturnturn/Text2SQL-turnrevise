from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import StrEnum
from typing import Any


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class RunStatus(StrEnum):
    RECEIVED = "RECEIVED"
    CONTEXT_BUILDING = "CONTEXT_BUILDING"
    LINKING = "LINKING"
    NEEDS_CLARIFICATION = "NEEDS_CLARIFICATION"
    PLANNING = "PLANNING"
    GENERATING = "GENERATING"
    VALIDATING = "VALIDATING"
    EXECUTING = "EXECUTING"
    CONTEXT_READY = "CONTEXT_READY"
    MODEL_RUNNING = "MODEL_RUNNING"
    TOOL_RUNNING = "TOOL_RUNNING"
    VERIFYING = "VERIFYING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


TERMINAL_STATUSES = frozenset(
    {
        RunStatus.COMPLETED,
        RunStatus.FAILED,
        RunStatus.CANCELLED,
        RunStatus.NEEDS_CLARIFICATION,
    }
)

ALLOWED_TRANSITIONS: dict[RunStatus, frozenset[RunStatus]] = {
    RunStatus.RECEIVED: frozenset(
        {
            RunStatus.CONTEXT_BUILDING,
            RunStatus.CONTEXT_READY,
            RunStatus.FAILED,
            RunStatus.CANCELLED,
        }
    ),
    RunStatus.CONTEXT_BUILDING: frozenset(
        {RunStatus.LINKING, RunStatus.FAILED, RunStatus.CANCELLED}
    ),
    RunStatus.LINKING: frozenset(
        {
            RunStatus.NEEDS_CLARIFICATION,
            RunStatus.PLANNING,
            RunStatus.FAILED,
            RunStatus.CANCELLED,
        }
    ),
    RunStatus.NEEDS_CLARIFICATION: frozenset(),
    RunStatus.PLANNING: frozenset(
        {RunStatus.GENERATING, RunStatus.FAILED, RunStatus.CANCELLED}
    ),
    RunStatus.GENERATING: frozenset(
        {
            RunStatus.NEEDS_CLARIFICATION,
            RunStatus.VALIDATING,
            RunStatus.TOOL_RUNNING,
            RunStatus.FAILED,
            RunStatus.CANCELLED,
        }
    ),
    RunStatus.VALIDATING: frozenset(
        {
            RunStatus.EXECUTING,
            RunStatus.GENERATING,
            RunStatus.FAILED,
            RunStatus.CANCELLED,
        }
    ),
    RunStatus.EXECUTING: frozenset(
        {RunStatus.VERIFYING, RunStatus.FAILED, RunStatus.CANCELLED}
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
            RunStatus.GENERATING,
            RunStatus.VERIFYING,
            RunStatus.FAILED,
            RunStatus.CANCELLED,
        }
    ),
    RunStatus.VERIFYING: frozenset(
        {
            RunStatus.GENERATING,
            RunStatus.COMPLETED,
            RunStatus.FAILED,
            RunStatus.CANCELLED,
        }
    ),
    RunStatus.COMPLETED: frozenset(),
    RunStatus.FAILED: frozenset(),
    RunStatus.CANCELLED: frozenset(),
}


class InvalidRunTransition(ValueError):
    pass


class HarnessBudgetExceeded(RuntimeError):
    pass


class UnsafeRecoveryError(RuntimeError):
    pass


class OperationKind(StrEnum):
    READ_QUERY = "READ_QUERY"
    BUSINESS_WRITE = "BUSINESS_WRITE"
    APPROVAL = "APPROVAL"


@dataclass(frozen=True)
class HarnessBudget:
    timeout_seconds: float = 120.0
    max_tool_calls: int = 8
    max_read_retries: int = 2
    max_model_calls: int = 6
    max_context_tokens: int = 8192
    max_clarification_turns: int = 2
    max_sql_validations: int = 3

    def __post_init__(self) -> None:
        if self.timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        if self.max_tool_calls < 1:
            raise ValueError("max_tool_calls must be at least 1")
        if self.max_read_retries < 0:
            raise ValueError("max_read_retries cannot be negative")
        if self.max_model_calls < 1:
            raise ValueError("max_model_calls must be at least 1")
        if self.max_context_tokens < 1:
            raise ValueError("max_context_tokens must be at least 1")
        if self.max_clarification_turns < 0:
            raise ValueError("max_clarification_turns cannot be negative")
        if self.max_sql_validations < 1:
            raise ValueError("max_sql_validations must be at least 1")


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
    operation_kind: OperationKind = OperationKind.READ_QUERY
    parent_run_id: str | None = None
    correlation_id: str | None = None

    def __post_init__(self) -> None:
        if self.correlation_id is None:
            self.correlation_id = self.run_id


@dataclass(frozen=True)
class RunStep:
    run_id: str
    status: RunStatus
    occurred_at: datetime = field(default_factory=utc_now)
    failure_type: str | None = None


@dataclass(frozen=True)
class RunArtifact:
    artifact_type: str
    content_hash: str
    storage_ref: str
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class RunCheckpoint:
    checkpoint_id: str
    run_id: str
    stage: RunStatus
    artifacts: list[dict[str, Any]]
    safe_to_resume: bool
    created_at: datetime = field(default_factory=utc_now)
