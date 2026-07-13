from __future__ import annotations

import json
from datetime import datetime

import psycopg
from psycopg.rows import dict_row

from app.harness.models import (
    ALLOWED_TRANSITIONS,
    TERMINAL_STATUSES,
    InvalidRunTransition,
    RunRecord,
    RunStatus,
    RunStep,
)


class PostgresRunStore:
    """Durable Harness state restricted to the agent_state schema."""

    def __init__(self, database_url: str, *, model_name: str, retrieval_mode: str):
        self.database_url = database_url
        self.model_name = model_name
        self.retrieval_mode = retrieval_mode

    async def _connect(self):
        return await psycopg.AsyncConnection.connect(
            self.database_url, row_factory=dict_row
        )

    @staticmethod
    def _record(row) -> RunRecord:
        return RunRecord(
            run_id=str(row["id"]),
            user_id=str(row["user_id"]),
            conversation_id=row["conversation_id"],
            instruction_hash=(row["metadata"] or {}).get("instruction_hash", ""),
            status=RunStatus(row["status"]),
            failure_type=row["failure_type"],
            tool_call_count=row["tool_call_count"],
            retry_count=row["retry_count"],
            created_at=row["started_at"],
            updated_at=row["updated_at"],
            completed_at=row["finished_at"],
        )

    async def create(self, record: RunRecord) -> None:
        async with await self._connect() as conn:
            async with conn.transaction():
                await conn.execute(
                    """INSERT INTO agent_state.agent_runs
                       (id,conversation_id,user_id,request_id,status,model_name,
                        retrieval_mode,retry_count,tool_call_count,metadata,started_at,updated_at)
                       VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s,%s)""",
                    (
                        record.run_id,
                        record.conversation_id,
                        record.user_id,
                        record.run_id,
                        record.status.value,
                        self.model_name,
                        self.retrieval_mode,
                        record.retry_count,
                        record.tool_call_count,
                        json.dumps({"instruction_hash": record.instruction_hash}),
                        record.created_at,
                        record.updated_at,
                    ),
                )
                await self._insert_step(conn, record.run_id, record.status)

    async def transition(
        self,
        run_id: str,
        status: RunStatus,
        *,
        failure_type: str | None = None,
    ) -> RunRecord:
        async with await self._connect() as conn:
            async with conn.transaction():
                row = await (await conn.execute(
                    "SELECT * FROM agent_state.agent_runs WHERE id=%s FOR UPDATE",
                    (run_id,),
                )).fetchone()
                if row is None:
                    raise KeyError(run_id)
                current = RunStatus(row["status"])
                if status not in ALLOWED_TRANSITIONS[current]:
                    raise InvalidRunTransition(
                        f"cannot transition run {run_id} from {current} to {status}"
                    )
                finished = "now()" if status in TERMINAL_STATUSES else "finished_at"
                row = await (await conn.execute(
                    f"""UPDATE agent_state.agent_runs
                        SET status=%s,failure_type=%s,updated_at=now(),finished_at={finished}
                        WHERE id=%s RETURNING *""",
                    (status.value, failure_type, run_id),
                )).fetchone()
                await self._insert_step(conn, run_id, status, failure_type)
                return self._record(row)

    async def _insert_step(
        self, conn, run_id: str, status: RunStatus, failure_type: str | None = None
    ) -> None:
        await conn.execute(
            """INSERT INTO agent_state.run_steps
               (run_id,sequence_no,stage,status,error_type)
               SELECT %s,COALESCE(MAX(sequence_no)+1,0),%s,%s,%s
               FROM agent_state.run_steps WHERE run_id=%s""",
            (run_id, status.value, status.value, failure_type, run_id),
        )

    async def get(self, run_id: str) -> RunRecord | None:
        async with await self._connect() as conn:
            row = await (await conn.execute(
                "SELECT * FROM agent_state.agent_runs WHERE id=%s", (run_id,)
            )).fetchone()
        return self._record(row) if row else None

    async def list_steps(self, run_id: str) -> list[RunStep]:
        async with await self._connect() as conn:
            rows = await (await conn.execute(
                """SELECT status,created_at,error_type FROM agent_state.run_steps
                   WHERE run_id=%s ORDER BY sequence_no""",
                (run_id,),
            )).fetchall()
        return [
            RunStep(run_id, RunStatus(row["status"]), row["created_at"], row["error_type"])
            for row in rows
        ]

    async def increment_tool_calls(self, run_id: str) -> int:
        return await self._increment(run_id, "tool_call_count")

    async def increment_retries(self, run_id: str) -> int:
        return await self._increment(run_id, "retry_count")

    async def _increment(self, run_id: str, column: str) -> int:
        if column not in {"tool_call_count", "retry_count"}:
            raise ValueError("unsupported counter")
        async with await self._connect() as conn:
            row = await (await conn.execute(
                f"""UPDATE agent_state.agent_runs SET {column}={column}+1,updated_at=now()
                    WHERE id=%s RETURNING {column}""",
                (run_id,),
            )).fetchone()
        if row is None:
            raise KeyError(run_id)
        return int(row[column])

    async def fail_incomplete_runs(
        self,
        *,
        failure_type: str = "process_restarted",
        before: datetime | None = None,
    ) -> int:
        terminal = [status.value for status in TERMINAL_STATUSES]
        async with await self._connect() as conn:
            async with conn.transaction():
                params: list[object] = [failure_type, terminal]
                condition = ""
                if before is not None:
                    condition = " AND updated_at < %s"
                    params.append(before)
                rows = await (await conn.execute(
                    f"""UPDATE agent_state.agent_runs
                        SET status='FAILED',failure_type=%s,updated_at=now(),finished_at=now()
                        WHERE NOT (status = ANY(%s)){condition}
                        RETURNING id""",
                    params,
                )).fetchall()
                for row in rows:
                    await self._insert_step(
                        conn, str(row["id"]), RunStatus.FAILED, failure_type
                    )
                return len(rows)
