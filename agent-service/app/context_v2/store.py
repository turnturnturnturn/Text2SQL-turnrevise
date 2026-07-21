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
    async def save_conversation_state(self, state: ConversationState) -> None: ...
    async def list_conversation_states(self, conversation_id: str) -> list[dict[str, Any]]: ...


class InMemoryContextStore:
    def __init__(self) -> None:
        self._manifests: dict[str, dict[str, Any]] = {}
        self._conversation_states: dict[str, list[dict[str, Any]]] = {}
        self._lock = asyncio.Lock()

    async def save_manifest(self, manifest: ContextManifest) -> None:
        async with self._lock:
            self._manifests[manifest.run_id] = deepcopy(manifest.to_redacted_dict())

    async def get_manifest(self, run_id: str) -> dict[str, Any] | None:
        async with self._lock:
            value = self._manifests.get(run_id)
            return deepcopy(value) if value else None

    async def save_conversation_state(self, state: ConversationState) -> None:
        async with self._lock:
            payload = asdict(state)
            self._conversation_states.setdefault(state.conversation_id, []).append(payload)

    async def list_conversation_states(self, conversation_id: str) -> list[dict[str, Any]]:
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
            await conn.execute(
                """INSERT INTO agent_state.context_manifests
                   (run_id,policy_version,mode,total_token_budget,actual_tokens,
                    manifest_payload,created_at)
                   VALUES (%s,%s,%s,%s,%s,%s::jsonb,%s)
                   ON CONFLICT (run_id) DO UPDATE SET
                     policy_version=EXCLUDED.policy_version,
                     mode=EXCLUDED.mode,
                     total_token_budget=EXCLUDED.total_token_budget,
                     actual_tokens=EXCLUDED.actual_tokens,
                     manifest_payload=EXCLUDED.manifest_payload""",
                (
                    manifest.run_id,
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
            row = await (await conn.execute(
                """SELECT manifest_payload FROM agent_state.context_manifests
                   WHERE run_id=%s""",
                (run_id,),
            )).fetchone()
        return dict(row["manifest_payload"]) if row else None

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

    async def list_conversation_states(self, conversation_id: str) -> list[dict[str, Any]]:
        async with await self._connect() as conn:
            rows = await (await conn.execute(
                """SELECT state_payload FROM agent_state.conversation_states
                   WHERE conversation_id=%s ORDER BY summary_version""",
                (conversation_id,),
            )).fetchall()
        return [dict(row["state_payload"]) for row in rows]
