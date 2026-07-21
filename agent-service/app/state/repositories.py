from __future__ import annotations

import json
from copy import deepcopy
from datetime import datetime, timezone
from typing import Any, Protocol

import psycopg
from psycopg.rows import dict_row

from app.state.models import (
    MemoryEvent,
    MemoryRecord,
    MemoryScope,
    MemoryStatus,
    MemoryType,
    MemoryValidity,
)


class StateRepository(Protocol):
    async def create_conversation(self, payload: dict[str, Any]) -> None: ...
    async def get_conversation(
        self, conversation_id: str, user_id: str
    ) -> dict[str, Any] | None: ...
    async def save_conversation(self, payload: dict[str, Any]) -> None: ...
    async def delete_conversation(self, conversation_id: str, user_id: str) -> bool: ...
    async def list_conversations(
        self, user_id: str, limit: int, offset: int
    ) -> list[dict[str, Any]]: ...
    async def upsert_memory(self, memory: MemoryRecord) -> MemoryRecord: ...
    async def get_memory(self, memory_id: str, user_id: str) -> MemoryRecord | None: ...
    async def list_memories(
        self,
        user_id: str,
        statuses: set[MemoryStatus] | None = None,
        *,
        include_global: bool = False,
    ) -> list[MemoryRecord]: ...
    async def set_memory_status(
        self, memory_id: str, user_id: str, status: MemoryStatus
    ) -> MemoryRecord | None: ...
    async def set_memory_validity(
        self, memory_id: str, user_id: str, validity: MemoryValidity
    ) -> MemoryRecord | None: ...
    async def delete_memory(self, memory_id: str, user_id: str) -> bool: ...
    async def add_memory_event(self, event: MemoryEvent) -> None: ...
    async def delete_expired_conversations(self, retention_days: int) -> int: ...


class InMemoryStateRepository:
    """Deterministic test repository with the same user isolation as PostgreSQL."""

    def __init__(self) -> None:
        self.conversations: dict[str, dict[str, Any]] = {}
        self.memories: dict[str, MemoryRecord] = {}
        self.memory_events: list[MemoryEvent] = []

    async def create_conversation(self, payload: dict[str, Any]) -> None:
        if payload["id"] in self.conversations:
            raise ValueError("conversation id already exists")
        self.conversations[payload["id"]] = deepcopy(payload)

    async def get_conversation(
        self, conversation_id: str, user_id: str
    ) -> dict[str, Any] | None:
        value = self.conversations.get(conversation_id)
        return deepcopy(value) if value and value["user"]["id"] == user_id else None

    async def save_conversation(self, payload: dict[str, Any]) -> None:
        current = self.conversations.get(payload["id"])
        if current and current["user"]["id"] != payload["user"]["id"]:
            raise PermissionError("conversation not found or belongs to another user")
        self.conversations[payload["id"]] = deepcopy(payload)

    async def delete_conversation(self, conversation_id: str, user_id: str) -> bool:
        if await self.get_conversation(conversation_id, user_id) is None:
            return False
        del self.conversations[conversation_id]
        return True

    async def list_conversations(
        self, user_id: str, limit: int, offset: int
    ) -> list[dict[str, Any]]:
        values = [v for v in self.conversations.values() if v["user"]["id"] == user_id]
        values.sort(key=lambda v: str(v.get("updated_at", "")), reverse=True)
        return deepcopy(values[offset : offset + limit])

    async def upsert_memory(self, memory: MemoryRecord) -> MemoryRecord:
        for existing in self.memories.values():
            if (
                existing.user_id,
                existing.memory_type,
                existing.normalized_content,
            ) == (memory.user_id, memory.memory_type, memory.normalized_content):
                existing.updated_at = datetime.now(timezone.utc)
                return deepcopy(existing)
        self.memories[memory.id] = deepcopy(memory)
        return deepcopy(memory)

    async def get_memory(self, memory_id: str, user_id: str) -> MemoryRecord | None:
        value = self.memories.get(memory_id)
        return deepcopy(value) if value and value.user_id == user_id else None

    async def list_memories(
        self,
        user_id: str,
        statuses: set[MemoryStatus] | None = None,
        *,
        include_global: bool = False,
    ) -> list[MemoryRecord]:
        now = datetime.now(timezone.utc)
        values = [
            m
            for m in self.memories.values()
            if (
                m.user_id == user_id
                or (include_global and m.scope == MemoryScope.GLOBAL)
            )
            and (statuses is None or m.status in statuses)
            and (m.expires_at is None or m.expires_at > now)
        ]
        values.sort(key=lambda m: m.updated_at, reverse=True)
        return deepcopy(values)

    async def set_memory_status(
        self, memory_id: str, user_id: str, status: MemoryStatus
    ) -> MemoryRecord | None:
        memory = self.memories.get(memory_id)
        if not memory or memory.user_id != user_id:
            return None
        memory.status = status
        memory.updated_at = datetime.now(timezone.utc)
        return deepcopy(memory)

    async def set_memory_validity(
        self, memory_id: str, user_id: str, validity: MemoryValidity
    ) -> MemoryRecord | None:
        memory = self.memories.get(memory_id)
        if not memory or memory.user_id != user_id:
            return None
        memory.validity = validity
        memory.updated_at = datetime.now(timezone.utc)
        return deepcopy(memory)

    async def delete_memory(self, memory_id: str, user_id: str) -> bool:
        if await self.get_memory(memory_id, user_id) is None:
            return False
        del self.memories[memory_id]
        return True

    async def add_memory_event(self, event: MemoryEvent) -> None:
        self.memory_events.append(deepcopy(event))

    async def delete_expired_conversations(self, retention_days: int) -> int:
        if retention_days < 1:
            raise ValueError("retention_days must be positive")
        cutoff = datetime.now(timezone.utc).timestamp() - retention_days * 86400
        expired = []
        for key, value in self.conversations.items():
            updated_at = value["updated_at"]
            if not isinstance(updated_at, datetime):
                updated_at = datetime.fromisoformat(str(updated_at))
            if updated_at.tzinfo is None:
                updated_at = updated_at.replace(tzinfo=timezone.utc)
            if updated_at.timestamp() < cutoff:
                expired.append(key)
        for key in expired:
            del self.conversations[key]
        return len(expired)


class PostgresStateRepository:
    """Small connection-per-operation repository; safe for the local single worker."""

    def __init__(self, database_url: str) -> None:
        self.database_url = database_url

    async def _connect(self):
        return await psycopg.AsyncConnection.connect(
            self.database_url, row_factory=dict_row
        )

    async def create_conversation(self, payload: dict[str, Any]) -> None:
        async with await self._connect() as conn:
            async with conn.transaction():
                await conn.execute(
                    """INSERT INTO agent_state.conversations
                       (id,user_id,user_snapshot,metadata,created_at,updated_at)
                       VALUES (%s,%s,%s::jsonb,%s::jsonb,%s,%s)""",
                    (
                        payload["id"],
                        payload["user"]["id"],
                        json.dumps(payload["user"]),
                        json.dumps(payload.get("metadata", {})),
                        payload["created_at"],
                        payload["updated_at"],
                    ),
                )
                await self._replace_messages(conn, payload)

    async def get_conversation(
        self, conversation_id: str, user_id: str
    ) -> dict[str, Any] | None:
        async with await self._connect() as conn:
            row = await (
                await conn.execute(
                    "SELECT * FROM agent_state.conversations WHERE id=%s AND user_id=%s",
                    (conversation_id, user_id),
                )
            ).fetchone()
            if not row:
                return None
            messages = await (
                await conn.execute(
                    "SELECT message_payload FROM agent_state.messages WHERE conversation_id=%s ORDER BY sequence_no",
                    (conversation_id,),
                )
            ).fetchall()
        return {
            "id": row["id"],
            "user": row["user_snapshot"],
            "messages": [m["message_payload"] for m in messages],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
            "metadata": row["metadata"],
        }

    async def save_conversation(self, payload: dict[str, Any]) -> None:
        async with await self._connect() as conn:
            async with conn.transaction():
                existing = await (
                    await conn.execute(
                        "SELECT user_id FROM agent_state.conversations WHERE id=%s FOR UPDATE",
                        (payload["id"],),
                    )
                ).fetchone()
                if existing and str(existing["user_id"]) != str(payload["user"]["id"]):
                    raise PermissionError(
                        "conversation not found or belongs to another user"
                    )
                await conn.execute(
                    """INSERT INTO agent_state.conversations
                       (id,user_id,user_snapshot,metadata,created_at,updated_at)
                       VALUES (%s,%s,%s::jsonb,%s::jsonb,%s,%s)
                       ON CONFLICT (id) DO UPDATE SET
                         user_snapshot=EXCLUDED.user_snapshot,
                         metadata=EXCLUDED.metadata,
                         updated_at=EXCLUDED.updated_at
                       WHERE agent_state.conversations.user_id=EXCLUDED.user_id""",
                    (
                        payload["id"],
                        payload["user"]["id"],
                        json.dumps(payload["user"]),
                        json.dumps(payload.get("metadata", {})),
                        payload["created_at"],
                        payload["updated_at"],
                    ),
                )
                await conn.execute(
                    "DELETE FROM agent_state.messages WHERE conversation_id=%s",
                    (payload["id"],),
                )
                await self._replace_messages(conn, payload)

    async def _replace_messages(self, conn, payload: dict[str, Any]) -> None:
        for sequence_no, message in enumerate(payload.get("messages", [])):
            await conn.execute(
                """INSERT INTO agent_state.messages
                   (conversation_id,sequence_no,role,content,message_payload,created_at)
                   VALUES (%s,%s,%s,%s,%s::jsonb,%s)""",
                (
                    payload["id"],
                    sequence_no,
                    message["role"],
                    message["content"],
                    json.dumps(message, default=str),
                    message.get("timestamp"),
                ),
            )

    async def delete_conversation(self, conversation_id: str, user_id: str) -> bool:
        async with await self._connect() as conn:
            result = await conn.execute(
                "DELETE FROM agent_state.conversations WHERE id=%s AND user_id=%s",
                (conversation_id, user_id),
            )
            return result.rowcount == 1

    async def list_conversations(
        self, user_id: str, limit: int, offset: int
    ) -> list[dict[str, Any]]:
        async with await self._connect() as conn:
            rows = await (
                await conn.execute(
                    """SELECT id FROM agent_state.conversations WHERE user_id=%s
                   ORDER BY updated_at DESC LIMIT %s OFFSET %s""",
                    (user_id, limit, offset),
                )
            ).fetchall()
        result = []
        for row in rows:
            value = await self.get_conversation(row["id"], user_id)
            if value:
                result.append(value)
        return result

    @staticmethod
    def _memory(row: dict[str, Any]) -> MemoryRecord:
        return MemoryRecord(
            id=str(row["id"]),
            user_id=str(row["user_id"]),
            memory_type=MemoryType(row["memory_type"]),
            scope=MemoryScope(row["scope"]),
            status=MemoryStatus(row["status"]),
            content=row["content"],
            normalized_content=row["normalized_content"],
            source=row["source"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            tool_name=row["tool_name"],
            tool_args=row["tool_args"],
            success=row["success"],
            embedding=list(row["embedding"]) if row["embedding"] else None,
            metadata=row["metadata"],
            expires_at=row["expires_at"],
            validity=MemoryValidity(row.get("validity") or MemoryValidity.ACTIVE.value),
            source_hash=row.get("source_hash"),
            conflict_key=row.get("conflict_key"),
            valid_from=row.get("valid_from"),
            valid_to=row.get("valid_to"),
        )

    async def upsert_memory(self, memory: MemoryRecord) -> MemoryRecord:
        async with await self._connect() as conn:
            row = await (
                await conn.execute(
                    """INSERT INTO agent_state.memories
                   (id,user_id,scope,memory_type,status,content,normalized_content,source,tool_name,tool_args,success,embedding,metadata,expires_at,validity,source_hash,conflict_key,valid_from,valid_to)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s,%s,%s::jsonb,%s,%s,%s,%s,%s,%s)
                   ON CONFLICT (user_id,memory_type,normalized_content) DO UPDATE SET updated_at=now()
                   RETURNING *""",
                    (
                        memory.id,
                        memory.user_id,
                        memory.scope.value,
                        memory.memory_type.value,
                        memory.status.value,
                        memory.content,
                        memory.normalized_content,
                        memory.source,
                        memory.tool_name,
                        json.dumps(memory.tool_args)
                        if memory.tool_args is not None
                        else None,
                        memory.success,
                        memory.embedding,
                        json.dumps(memory.metadata),
                        memory.expires_at,
                        memory.validity.value,
                        memory.source_hash,
                        memory.conflict_key,
                        memory.valid_from,
                        memory.valid_to,
                    ),
                )
            ).fetchone()
        return self._memory(row)

    async def get_memory(self, memory_id: str, user_id: str) -> MemoryRecord | None:
        async with await self._connect() as conn:
            row = await (
                await conn.execute(
                    "SELECT * FROM agent_state.memories WHERE id=%s AND user_id=%s",
                    (memory_id, user_id),
                )
            ).fetchone()
        return self._memory(row) if row else None

    async def list_memories(
        self,
        user_id: str,
        statuses: set[MemoryStatus] | None = None,
        *,
        include_global: bool = False,
    ) -> list[MemoryRecord]:
        params: list[Any] = [user_id]
        sql = "SELECT * FROM agent_state.memories WHERE (user_id=%s"
        if include_global:
            sql += " OR scope='GLOBAL'"
        sql += ")"
        if statuses:
            sql += " AND status = ANY(%s)"
            params.append([s.value for s in statuses])
        sql += (
            " AND (expires_at IS NULL OR expires_at > now()) ORDER BY updated_at DESC"
        )
        async with await self._connect() as conn:
            rows = await (await conn.execute(sql, params)).fetchall()
        return [self._memory(row) for row in rows]

    async def set_memory_status(
        self, memory_id: str, user_id: str, status: MemoryStatus
    ) -> MemoryRecord | None:
        async with await self._connect() as conn:
            row = await (
                await conn.execute(
                    """UPDATE agent_state.memories SET status=%s,updated_at=now()
                   WHERE id=%s AND user_id=%s RETURNING *""",
                    (status.value, memory_id, user_id),
                )
            ).fetchone()
        return self._memory(row) if row else None

    async def confirm_memory_atomic(
        self, memory_id: str, user_id: str
    ) -> MemoryRecord | None:
        """Confirm and supersede same-key memories under one per-key DB lock."""
        async with await self._connect() as conn:
            async with conn.transaction():
                target = await (
                    await conn.execute(
                        "SELECT * FROM agent_state.memories WHERE id=%s AND user_id=%s",
                        (memory_id, user_id),
                    )
                ).fetchone()
                if target is None:
                    return None
                conflict_key = target.get("conflict_key")
                if conflict_key and (target.get("validity") or "ACTIVE") == "ACTIVE":
                    await conn.execute(
                        "SELECT pg_advisory_xact_lock(hashtext(%s))",
                        (f"{user_id}:{conflict_key}",),
                    )
                    target = await (
                        await conn.execute(
                            "SELECT * FROM agent_state.memories WHERE id=%s AND user_id=%s FOR UPDATE",
                            (memory_id, user_id),
                        )
                    ).fetchone()
                    superseded = await (
                        await conn.execute(
                            """UPDATE agent_state.memories
                           SET validity='SUPERSEDED',valid_to=now(),updated_at=now()
                           WHERE user_id=%s AND conflict_key=%s AND id<>%s
                             AND status='confirmed' AND validity='ACTIVE'
                           RETURNING id""",
                            (user_id, conflict_key, memory_id),
                        )
                    ).fetchall()
                    for row in superseded:
                        await conn.execute(
                            """INSERT INTO agent_state.memory_events
                               (memory_id,user_id,event_type,details)
                               VALUES (%s,%s,'superseded',%s::jsonb)""",
                            (
                                row["id"],
                                user_id,
                                json.dumps({"superseded_by": memory_id}),
                            ),
                        )
                confirmed = await (
                    await conn.execute(
                        """UPDATE agent_state.memories
                       SET status='confirmed',updated_at=now()
                       WHERE id=%s AND user_id=%s RETURNING *""",
                        (memory_id, user_id),
                    )
                ).fetchone()
                await conn.execute(
                    """INSERT INTO agent_state.memory_events
                       (memory_id,user_id,event_type,details)
                       VALUES (%s,%s,'confirmed','{}'::jsonb)""",
                    (memory_id, user_id),
                )
                return self._memory(confirmed)

    async def set_memory_validity(
        self, memory_id: str, user_id: str, validity: MemoryValidity
    ) -> MemoryRecord | None:
        async with await self._connect() as conn:
            row = await (
                await conn.execute(
                    """UPDATE agent_state.memories SET validity=%s,updated_at=now()
                   WHERE id=%s AND user_id=%s RETURNING *""",
                    (validity.value, memory_id, user_id),
                )
            ).fetchone()
        return self._memory(row) if row else None

    async def delete_memory(self, memory_id: str, user_id: str) -> bool:
        async with await self._connect() as conn:
            result = await conn.execute(
                "DELETE FROM agent_state.memories WHERE id=%s AND user_id=%s",
                (memory_id, user_id),
            )
            return result.rowcount == 1

    async def add_memory_event(self, event: MemoryEvent) -> None:
        async with await self._connect() as conn:
            await conn.execute(
                """INSERT INTO agent_state.memory_events(memory_id,user_id,event_type,details)
                   VALUES (%s,%s,%s,%s::jsonb)""",
                (
                    event.memory_id,
                    event.user_id,
                    event.event_type,
                    json.dumps(event.details),
                ),
            )

    async def delete_expired_conversations(self, retention_days: int) -> int:
        if retention_days < 1:
            raise ValueError("retention_days must be positive")
        async with await self._connect() as conn:
            result = await conn.execute(
                """DELETE FROM agent_state.conversations
                   WHERE updated_at < now() - (%s * interval '1 day')""",
                (retention_days,),
            )
            return result.rowcount
