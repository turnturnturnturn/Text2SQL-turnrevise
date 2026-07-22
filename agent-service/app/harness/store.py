from __future__ import annotations

import asyncio
import hashlib
import uuid
from copy import deepcopy
from datetime import datetime
from typing import Protocol

from app.harness.models import (
    ALLOWED_TRANSITIONS,
    TERMINAL_STATUSES,
    InvalidRunTransition,
    RunRecord,
    RunCheckpoint,
    OperationKind,
    UnsafeRecoveryError,
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

    async def add_checkpoint(
        self,
        run_id: str,
        *,
        stage: RunStatus,
        artifacts: list[dict],
        safe_to_resume: bool,
    ) -> RunCheckpoint: ...
    async def list_checkpoints(self, run_id: str) -> list[RunCheckpoint]: ...
    async def cancel(self, run_id: str, user_id: str) -> RunRecord: ...
    async def create_recovery_child(
        self, parent_run_id: str, user_id: str, instruction: str
    ) -> RunRecord: ...


class InMemoryRunStore:
    """Concurrency-safe development store with PostgreSQL-compatible semantics."""

    def __init__(self) -> None:
        self._records: dict[str, RunRecord] = {}
        self._steps: dict[str, list[RunStep]] = {}
        self._checkpoints: dict[str, list[RunCheckpoint]] = {}
        self._lock = asyncio.Lock()

    async def create(self, record: RunRecord) -> None:
        async with self._lock:
            if record.run_id in self._records:
                raise ValueError(f"run already exists: {record.run_id}")
            self._records[record.run_id] = deepcopy(record)
            self._steps[record.run_id] = [
                RunStep(record.run_id, record.status, record.created_at)
            ]
            self._checkpoints[record.run_id] = []

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

    async def add_checkpoint(
        self,
        run_id: str,
        *,
        stage: RunStatus,
        artifacts: list[dict],
        safe_to_resume: bool,
    ) -> RunCheckpoint:
        allowed_keys = {"artifact_type", "content_hash", "storage_ref", "metadata"}
        clean_artifacts = []
        for artifact in artifacts:
            if set(artifact) - allowed_keys:
                raise ValueError("checkpoint artifacts may contain references only")
            if not all(
                artifact.get(key)
                for key in ("artifact_type", "content_hash", "storage_ref")
            ):
                raise ValueError("checkpoint artifact reference is incomplete")
            clean_artifacts.append(deepcopy(artifact))
        async with self._lock:
            if run_id not in self._records:
                raise KeyError(run_id)
            checkpoint = RunCheckpoint(
                checkpoint_id=str(uuid.uuid4()),
                run_id=run_id,
                stage=stage,
                artifacts=clean_artifacts,
                safe_to_resume=safe_to_resume,
            )
            self._checkpoints[run_id].append(deepcopy(checkpoint))
            return deepcopy(checkpoint)

    async def list_checkpoints(self, run_id: str) -> list[RunCheckpoint]:
        async with self._lock:
            return deepcopy(self._checkpoints.get(run_id, []))

    async def cancel(self, run_id: str, user_id: str) -> RunRecord:
        record = await self.get(run_id)
        if record is None:
            raise KeyError(run_id)
        if record.user_id != user_id:
            raise PermissionError("run belongs to another user")
        if record.status in TERMINAL_STATUSES:
            return record
        return await self.transition(
            run_id, RunStatus.CANCELLED, failure_type="cancelled"
        )

    async def create_recovery_child(
        self, parent_run_id: str, user_id: str, instruction: str
    ) -> RunRecord:
        parent = await self.get(parent_run_id)
        if parent is None:
            raise KeyError(parent_run_id)
        if parent.user_id != user_id:
            raise PermissionError("run belongs to another user")
        if parent.operation_kind != OperationKind.READ_QUERY:
            raise UnsafeRecoveryError("only read-only query runs may be recovered")
        if (
            parent.status != RunStatus.FAILED
            or parent.failure_type != "process_restarted"
        ):
            raise UnsafeRecoveryError("parent is not a restart-closed run")
        checkpoints = await self.list_checkpoints(parent_run_id)
        allowed_stages = {
            RunStatus.CONTEXT_BUILDING,
            RunStatus.LINKING,
            RunStatus.PLANNING,
            RunStatus.VALIDATING,
        }
        allowed_artifacts = {
            "context_manifest",
            "schema_link",
            "query_plan",
            "validation",
        }
        latest = checkpoints[-1] if checkpoints else None
        if (
            latest is None
            or not latest.safe_to_resume
            or latest.stage not in allowed_stages
            or not latest.artifacts
            or any(
                artifact.get("artifact_type") not in allowed_artifacts
                for artifact in latest.artifacts
            )
        ):
            raise UnsafeRecoveryError("parent has no safe checkpoint")
        child = RunRecord(
            run_id=str(uuid.uuid4()),
            user_id=user_id,
            conversation_id=parent.conversation_id,
            instruction_hash=hashlib.sha256(instruction.encode("utf-8")).hexdigest(),
            operation_kind=OperationKind.READ_QUERY,
            parent_run_id=parent.run_id,
            correlation_id=parent.correlation_id,
        )
        await self.create(child)
        return child
