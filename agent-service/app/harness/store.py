from __future__ import annotations

import asyncio
from copy import deepcopy
from datetime import datetime
from typing import Protocol

from app.harness.models import (
    ALLOWED_TRANSITIONS,
    TERMINAL_STATUSES,
    InvalidRunTransition,
    RunRecord,
    RunStatus,
    RunStep,
    utc_now,
)


class RunStore(Protocol):
    """Persistence boundary implemented by memory and PostgreSQL stores."""

    async def create(self, record: RunRecord) -> None: ...

    async def transition(
        self,
        run_id: str,
        status: RunStatus,
        *,
        failure_type: str | None = None,
    ) -> RunRecord: ...

    async def get(self, run_id: str) -> RunRecord | None: ...

    async def list_steps(self, run_id: str) -> list[RunStep]: ...

    async def increment_tool_calls(self, run_id: str) -> int: ...

    async def increment_retries(self, run_id: str) -> int: ...

    async def fail_incomplete_runs(
        self,
        *,
        failure_type: str = "process_restarted",
        before: datetime | None = None,
    ) -> int: ...


class InMemoryRunStore:
    """Concurrency-safe development store with PostgreSQL-compatible semantics."""

    def __init__(self) -> None:
        self._records: dict[str, RunRecord] = {}
        self._steps: dict[str, list[RunStep]] = {}
        self._lock = asyncio.Lock()

    async def create(self, record: RunRecord) -> None:
        async with self._lock:
            if record.run_id in self._records:
                raise ValueError(f"run already exists: {record.run_id}")
            self._records[record.run_id] = deepcopy(record)
            self._steps[record.run_id] = [
                RunStep(record.run_id, record.status, record.created_at)
            ]

    async def transition(
        self,
        run_id: str,
        status: RunStatus,
        *,
        failure_type: str | None = None,
    ) -> RunRecord:
        async with self._lock:
            record = self._records.get(run_id)
            if record is None:
                raise KeyError(run_id)
            if status not in ALLOWED_TRANSITIONS[record.status]:
                raise InvalidRunTransition(
                    f"cannot transition run {run_id} from {record.status} to {status}"
                )
            now = utc_now()
            record.status = status
            record.failure_type = failure_type
            record.updated_at = now
            if status in TERMINAL_STATUSES:
                record.completed_at = now
            self._steps[run_id].append(
                RunStep(run_id, status, now, failure_type=failure_type)
            )
            return deepcopy(record)

    async def get(self, run_id: str) -> RunRecord | None:
        async with self._lock:
            record = self._records.get(run_id)
            return deepcopy(record) if record is not None else None

    async def list_steps(self, run_id: str) -> list[RunStep]:
        async with self._lock:
            return deepcopy(self._steps.get(run_id, []))

    async def increment_tool_calls(self, run_id: str) -> int:
        async with self._lock:
            record = self._records.get(run_id)
            if record is None:
                raise KeyError(run_id)
            record.tool_call_count += 1
            record.updated_at = utc_now()
            return record.tool_call_count

    async def increment_retries(self, run_id: str) -> int:
        async with self._lock:
            record = self._records.get(run_id)
            if record is None:
                raise KeyError(run_id)
            record.retry_count += 1
            record.updated_at = utc_now()
            return record.retry_count

    async def fail_incomplete_runs(
        self,
        *,
        failure_type: str = "process_restarted",
        before: datetime | None = None,
    ) -> int:
        async with self._lock:
            now = utc_now()
            count = 0
            for run_id, record in self._records.items():
                if record.status in TERMINAL_STATUSES:
                    continue
                if before is not None and record.updated_at >= before:
                    continue
                record.status = RunStatus.FAILED
                record.failure_type = failure_type
                record.updated_at = now
                record.completed_at = now
                self._steps[run_id].append(
                    RunStep(
                        run_id,
                        RunStatus.FAILED,
                        now,
                        failure_type=failure_type,
                    )
                )
                count += 1
            return count
