from __future__ import annotations

import asyncio
import json
from copy import deepcopy
from dataclasses import asdict
from typing import Any, Protocol

import psycopg
from psycopg.rows import dict_row

from app.context_v2.conversation import ConversationState
from app.context_v2.models import ContextManifest


class ContextStore(Protocol):
    async def save_manifest(self, manifest: ContextManifest) -> None: ...
    async def get_manifest(self, run_id: str) -> dict[str, Any] | None: ...
    async def list_manifests(self, run_id: str) -> list[dict[str, Any]]: ...
    async def save_conversation_state(self, state: ConversationState) -> None: ...
    async def list_conversation_states(
        self, conversation_id: str
    ) -> list[dict[str, Any]]: ...


class InMemoryContextStore:
    def __init__(self) -> None:
        self._manifests: dict[str, list[dict[str, Any]]] = {}
        self._conversation_states: dict[str, list[dict[str, Any]]] = {}
        self._lock = asyncio.Lock()

    async def save_manifest(self, manifest: ContextManifest) -> None:
        async with self._lock:
            versions = self._manifests.setdefault(manifest.run_id, [])
            payload = deepcopy(manifest.to_redacted_dict())
            payload["sequence_no"] = len(versions)
            versions.append(payload)

    async def get_manifest(self, run_id: str) -> dict[str, Any] | None:
        async with self._lock:
            values = self._manifests.get(run_id, [])
            return deepcopy(values[-1]) if values else None

    async def list_manifests(self, run_id: str) -> list[dict[str, Any]]:
        async with self._lock:
            return deepcopy(self._manifests.get(run_id, []))

    async def save_conversation_state(self, state: ConversationState) -> None:
        async with self._lock:
            payload = asdict(state)
            self._conversation_states.setdefault(state.conversation_id, []).append(
                payload
            )

    async def list_conversation_states(
        self, conversation_id: str
    ) -> list[dict[str, Any]]:
        async with self._lock:
            return deepcopy(self._conversation_states.get(conversation_id, []))


class PostgresContextStore:
    def __init__(self, database_url: str) -> None:
        self.database_url = database_url

    async def _connect(self):
        return await psycopg.AsyncConnection.connect(
            self.database_url, row_factory=dict_row
        )

    async def save_manifest(self, manifest: ContextManifest) -> None:
        payload = manifest.to_redacted_dict()
        async with await self._connect() as conn:
            async with conn.transaction():
                await conn.execute(
                    "SELECT pg_advisory_xact_lock(hashtext(%s))",
                    (manifest.run_id,),
                )
                row = await (
                    await conn.execute(
                        """SELECT COALESCE(MAX(sequence_no) + 1, 0) AS sequence_no
                       FROM agent_state.context_manifests WHERE run_id=%s""",
                        (manifest.run_id,),
                    )
                ).fetchone()
                sequence_no = int(row["sequence_no"])
                payload["sequence_no"] = sequence_no
                await conn.execute(
                    """INSERT INTO agent_state.context_manifests
                       (run_id,sequence_no,policy_version,mode,total_token_budget,
                        actual_tokens,manifest_payload,created_at)
                       VALUES (%s,%s,%s,%s,%s,%s,%s::jsonb,%s)""",
                    (
                        manifest.run_id,
                        sequence_no,
                        manifest.policy_version,
                        manifest.mode,
                        manifest.total_token_budget,
                        manifest.actual_tokens,
                        json.dumps(payload),
                        manifest.created_at,
                    ),
                )

    async def get_manifest(self, run_id: str) -> dict[str, Any] | None:
        async with await self._connect() as conn:
            row = await (
                await conn.execute(
                    """SELECT manifest_payload FROM agent_state.context_manifests
                   WHERE run_id=%s ORDER BY sequence_no DESC LIMIT 1""",
                    (run_id,),
                )
            ).fetchone()
        return dict(row["manifest_payload"]) if row else None

    async def list_manifests(self, run_id: str) -> list[dict[str, Any]]:
        async with await self._connect() as conn:
            rows = await (
                await conn.execute(
                    """SELECT manifest_payload FROM agent_state.context_manifests
                   WHERE run_id=%s ORDER BY sequence_no""",
                    (run_id,),
                )
            ).fetchall()
        return [dict(row["manifest_payload"]) for row in rows]

    async def save_conversation_state(self, state: ConversationState) -> None:
        payload = asdict(state)
        async with await self._connect() as conn:
            await conn.execute(
                """INSERT INTO agent_state.conversation_states
                   (conversation_id,summary_version,state_payload,source_message_ids,
                    model_id,created_at)
                   VALUES (%s,%s,%s::jsonb,%s,%s,%s)
                   ON CONFLICT (conversation_id,summary_version) DO NOTHING""",
                (
                    state.conversation_id,
                    state.summary_version,
                    json.dumps(payload, default=str),
                    list(state.source_message_ids),
                    state.model_id,
                    state.created_at,
                ),
            )

    async def list_conversation_states(
        self, conversation_id: str
    ) -> list[dict[str, Any]]:
        async with await self._connect() as conn:
            rows = await (
                await conn.execute(
                    """SELECT state_payload FROM agent_state.conversation_states
                   WHERE conversation_id=%s ORDER BY summary_version""",
                    (conversation_id,),
                )
            ).fetchall()
        return [dict(row["state_payload"]) for row in rows]
