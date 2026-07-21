from __future__ import annotations

import asyncio
import hashlib
import json
import uuid
import secrets
from copy import deepcopy
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Protocol

import psycopg
from psycopg.rows import dict_row

from app.harness.models import OperationKind, RunRecord, RunStatus, utc_now
from app.harness.store import RunStore


class ClarificationAlreadyAnswered(RuntimeError):
    pass


class ClarificationExpired(RuntimeError):
    pass


class ResumeReplay(RuntimeError): pass
class ResumeExpired(RuntimeError): pass
class ResumeInvalid(RuntimeError): pass
class ResumeNotFound(RuntimeError): pass


@dataclass(frozen=True, slots=True)
class ClarificationOption:
    option_id: str
    label: str
    value_id: str
    evidence_id: str
    source_hash: str


@dataclass(frozen=True, slots=True)
class MaterialAmbiguity:
    ambiguity_id: str
    question: str
    options: tuple[ClarificationOption, ...]
    reason: str


@dataclass(slots=True)
class ClarificationCard:
    card_id: str
    parent_run_id: str
    user_id: str
    ambiguity_id: str
    question: str
    options: tuple[ClarificationOption, ...]
    reason: str
    expires_at: datetime
    created_at: datetime = field(default_factory=utc_now)
    answered_at: datetime | None = None
    selected_option_id: str | None = None
    child_run_id: str | None = None


@dataclass(slots=True)
class ResumeTokenRecord:
    child_run_id: str
    parent_run_id: str
    user_id: str
    token_hash: str
    evidence_hash: str
    expires_at: datetime
    consumed_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class ClarificationResolution:
    child_run_id: str
    parent_run_id: str
    resume_token: str
    expires_at: datetime
    selection: dict[str, str]


class ClarificationStore(Protocol):
    async def save(self, card: ClarificationCard) -> None: ...
    async def get_for_parent(self, parent_run_id: str) -> ClarificationCard | None: ...


class InMemoryClarificationStore:
    def __init__(self) -> None:
        self._cards: dict[str, ClarificationCard] = {}
        self._resume_tokens: dict[str, ResumeTokenRecord] = {}
        self._lock = asyncio.Lock()

    async def save(self, card: ClarificationCard) -> None:
        async with self._lock:
            self._cards[card.parent_run_id] = deepcopy(card)

    async def get_for_parent(self, parent_run_id: str) -> ClarificationCard | None:
        async with self._lock:
            card = self._cards.get(parent_run_id)
            return deepcopy(card) if card else None

    async def save_resume(self, record: ResumeTokenRecord) -> None:
        async with self._lock:
            self._resume_tokens[record.child_run_id] = deepcopy(record)

    async def claim_resume(self, child_run_id: str, user_id: str, token_hash: str) -> None:
        async with self._lock:
            record = self._resume_tokens.get(child_run_id)
            if record is None or record.user_id != user_id:
                raise ResumeNotFound("resume not found")
            if record.consumed_at is not None:
                raise ResumeReplay("resume token already consumed")
            if record.expires_at <= utc_now():
                raise ResumeExpired("resume token expired")
            if not secrets.compare_digest(record.token_hash, token_hash):
                raise ResumeInvalid("resume token is invalid")
            record.consumed_at = utc_now()


class PostgresClarificationStore:
    def __init__(self, database_url: str) -> None:
        self.database_url = database_url

    async def _connect(self):
        return await psycopg.AsyncConnection.connect(
            self.database_url, row_factory=dict_row
        )

    @staticmethod
    def _option(payload: dict) -> ClarificationOption:
        return ClarificationOption(
            option_id=payload["option_id"],
            label=payload["label"],
            value_id=payload["value_id"],
            evidence_id=payload["evidence_id"],
            source_hash=payload["source_hash"],
        )

    async def save(self, card: ClarificationCard) -> None:
        options = [
            {
                "option_id": entry.option_id,
                "label": entry.label,
                "value_id": entry.value_id,
                "evidence_id": entry.evidence_id,
                "source_hash": entry.source_hash,
            }
            for entry in card.options
        ]
        async with await self._connect() as conn:
            await conn.execute(
                """INSERT INTO agent_state.clarification_requests
                   (id,parent_run_id,user_id,ambiguity_id,question,options,reason,
                    expires_at,created_at,answered_at,selected_option_id,child_run_id)
                   VALUES (%s,%s,%s,%s,%s,%s::jsonb,%s,%s,%s,%s,%s,%s)
                   ON CONFLICT (parent_run_id) DO NOTHING""",
                (
                    card.card_id,
                    card.parent_run_id,
                    card.user_id,
                    card.ambiguity_id,
                    card.question,
                    json.dumps(options),
                    card.reason,
                    card.expires_at,
                    card.created_at,
                    card.answered_at,
                    card.selected_option_id,
                    card.child_run_id,
                ),
            )

    async def get_for_parent(self, parent_run_id: str) -> ClarificationCard | None:
        async with await self._connect() as conn:
            row = await (
                await conn.execute(
                    """SELECT * FROM agent_state.clarification_requests
                   WHERE parent_run_id=%s""",
                    (parent_run_id,),
                )
            ).fetchone()
        if row is None:
            return None
        return ClarificationCard(
            card_id=str(row["id"]),
            parent_run_id=str(row["parent_run_id"]),
            user_id=str(row["user_id"]),
            ambiguity_id=row["ambiguity_id"],
            question=row["question"],
            options=tuple(self._option(payload) for payload in row["options"]),
            reason=row["reason"],
            expires_at=row["expires_at"],
            created_at=row["created_at"],
            answered_at=row["answered_at"],
            selected_option_id=row["selected_option_id"],
            child_run_id=(str(row["child_run_id"]) if row["child_run_id"] else None),
        )

    async def answer_atomic(
        self,
        parent_run_id: str,
        user_id: str,
        option_id: str,
        resume_ttl_seconds: int | None = None,
    ) -> RunRecord | ClarificationResolution:
        """Consume a card and create its child exactly once in one DB transaction."""
        resume_token = secrets.token_urlsafe(32) if resume_ttl_seconds else None
        async with await self._connect() as conn:
            async with conn.transaction():
                row = await (
                    await conn.execute(
                        """SELECT c.*, r.conversation_id, r.metadata AS run_metadata,
                              r.model_name, r.retrieval_mode, r.correlation_id
                       FROM agent_state.clarification_requests c
                       JOIN agent_state.agent_runs r ON r.id=c.parent_run_id
                       WHERE c.parent_run_id=%s
                       FOR UPDATE OF c""",
                        (parent_run_id,),
                    )
                ).fetchone()
                if row is None:
                    raise KeyError(parent_run_id)
                if str(row["user_id"]) != user_id:
                    raise PermissionError("clarification belongs to another user")
                if row["answered_at"] is not None:
                    raise ClarificationAlreadyAnswered("clarification already answered")
                if row["expires_at"] <= utc_now():
                    raise ClarificationExpired("clarification has expired")
                selected = next(
                    (
                        payload
                        for payload in row["options"]
                        if payload["option_id"] == option_id
                    ),
                    None,
                )
                if selected is None:
                    raise ValueError("unknown clarification option")
                parent_hash = (row["run_metadata"] or {}).get("instruction_hash", "")
                digest_input = (
                    parent_hash
                    + "\n"
                    + selected["evidence_id"]
                    + "\n"
                    + selected["source_hash"]
                )
                child_id = str(uuid.uuid4())
                instruction_hash = hashlib.sha256(
                    digest_input.encode("utf-8")
                ).hexdigest()
                metadata = {
                    "instruction_hash": instruction_hash,
                    "clarification": {
                        "option_id": option_id,
                        "value_id": selected["value_id"],
                        "evidence_id": selected["evidence_id"],
                        "source_hash": selected["source_hash"],
                        "trust_level": "user_selected_evidence",
                    },
                }
                await conn.execute(
                    """INSERT INTO agent_state.agent_runs
                       (id,conversation_id,user_id,request_id,status,model_name,
                        retrieval_mode,retry_count,tool_call_count,metadata,
                        started_at,updated_at,operation_kind,parent_run_id,correlation_id)
                       VALUES (%s,%s,%s,%s,'RECEIVED',%s,%s,0,0,%s::jsonb,
                               now(),now(),'READ_QUERY',%s,%s)""",
                    (
                        child_id,
                        row["conversation_id"],
                        user_id,
                        child_id,
                        row["model_name"],
                        row["retrieval_mode"],
                        json.dumps(metadata),
                        parent_run_id,
                        row["correlation_id"] or parent_run_id,
                    ),
                )
                await conn.execute(
                    """INSERT INTO agent_state.run_steps
                       (run_id,sequence_no,stage,status)
                       VALUES (%s,0,'RECEIVED','RECEIVED')""",
                    (child_id,),
                )
                updated = await (
                    await conn.execute(
                        """UPDATE agent_state.clarification_requests
                       SET answered_at=now(),selected_option_id=%s,child_run_id=%s
                       WHERE parent_run_id=%s AND answered_at IS NULL
                       RETURNING answered_at""",
                        (option_id, child_id, parent_run_id),
                    )
                ).fetchone()
                if updated is None:
                    raise ClarificationAlreadyAnswered("clarification already answered")
                child = RunRecord(
                    run_id=child_id,
                    user_id=user_id,
                    conversation_id=row["conversation_id"],
                    instruction_hash=instruction_hash,
                    operation_kind=OperationKind.READ_QUERY,
                    parent_run_id=parent_run_id,
                    correlation_id=str(row["correlation_id"] or parent_run_id),
                )
                if resume_token is None or resume_ttl_seconds is None:
                    return child
                expires_at = utc_now() + timedelta(seconds=resume_ttl_seconds)
                evidence_hash = hashlib.sha256(
                    f"{selected['evidence_id']}\n{selected['source_hash']}".encode("utf-8")
                ).hexdigest()
                await conn.execute(
                    """INSERT INTO agent_state.clarification_resume_tokens
                       (token_hash,child_run_id,parent_run_id,user_id,tenant_id,
                        evidence_hash,expires_at)
                       VALUES (%s,%s,%s,%s,'default',%s,%s)""",
                    (
                        hashlib.sha256(resume_token.encode("utf-8")).hexdigest(),
                        child_id,
                        parent_run_id,
                        user_id,
                        evidence_hash,
                        expires_at,
                    ),
                )
                return ClarificationResolution(
                    child_run_id=child_id,
                    parent_run_id=parent_run_id,
                    resume_token=resume_token,
                    expires_at=expires_at,
                    selection={
                        "option_id": selected["option_id"],
                        "label": selected["label"],
                        "evidence_id": selected["evidence_id"],
                        "source_hash": selected["source_hash"],
                    },
                )

    async def answer_atomic_with_resume(
        self, parent_run_id: str, user_id: str, option_id: str, ttl_seconds: int
    ) -> ClarificationResolution:
        result = await self.answer_atomic(
            parent_run_id, user_id, option_id, ttl_seconds
        )
        assert isinstance(result, ClarificationResolution)
        return result

    async def claim_resume_atomic(
        self, child_run_id: str, user_id: str, token: str
    ) -> RunRecord:
        token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
        async with await self._connect() as conn:
            async with conn.transaction():
                row = await (
                    await conn.execute(
                        """SELECT t.*, child.*, child.id AS child_id,
                                  parent.status AS parent_status
                           FROM agent_state.agent_runs child
                           LEFT JOIN agent_state.clarification_resume_tokens t
                             ON t.child_run_id=child.id
                           LEFT JOIN agent_state.agent_runs parent
                             ON parent.id=child.parent_run_id
                           WHERE child.id=%s
                           FOR UPDATE OF child,t""",
                        (child_run_id,),
                    )
                ).fetchone()
                if row is None or str(row["user_id"]) != user_id:
                    raise ResumeNotFound("resume not found")
                if row.get("token_hash") is None:
                    raise ResumeInvalid("resume token is invalid")
                if row["consumed_at"] is not None or row["status"] != "RECEIVED":
                    raise ResumeReplay("resume token already consumed")
                if row["expires_at"] <= utc_now():
                    raise ResumeExpired("resume token expired")
                if not secrets.compare_digest(row["token_hash"], token_hash):
                    raise ResumeInvalid("resume token is invalid")
                if row["operation_kind"] != "READ_QUERY" or row["parent_status"] != "NEEDS_CLARIFICATION":
                    raise ResumeInvalid("resume boundary is invalid")
                await conn.execute(
                    "UPDATE agent_state.clarification_resume_tokens SET consumed_at=now() WHERE child_run_id=%s",
                    (child_run_id,),
                )
                updated = await (
                    await conn.execute(
                        """UPDATE agent_state.agent_runs
                           SET status='CONTEXT_BUILDING',updated_at=now()
                           WHERE id=%s AND status='RECEIVED' RETURNING *""",
                        (child_run_id,),
                    )
                ).fetchone()
                if updated is None:
                    raise ResumeReplay("resume child already started")
                await conn.execute(
                    """INSERT INTO agent_state.run_steps
                       (run_id,sequence_no,stage,status)
                       SELECT %s,COALESCE(MAX(sequence_no)+1,0),'CONTEXT_BUILDING','CONTEXT_BUILDING'
                       FROM agent_state.run_steps WHERE run_id=%s""",
                    (child_run_id, child_run_id),
                )
                return RunRecord(
                    run_id=child_run_id,
                    user_id=user_id,
                    conversation_id=updated["conversation_id"],
                    instruction_hash=(updated["metadata"] or {}).get("instruction_hash", ""),
                    status=RunStatus.CONTEXT_BUILDING,
                    operation_kind=OperationKind.READ_QUERY,
                    parent_run_id=str(updated["parent_run_id"]),
                    correlation_id=str(updated["correlation_id"]),
                )


class ClarificationService:
    def __init__(
        self,
        run_store: RunStore,
        card_store: ClarificationStore,
        *,
        ttl_seconds: int = 900,
        resume_ttl_seconds: int = 300,
    ) -> None:
        if ttl_seconds < 1:
            raise ValueError("ttl_seconds must be positive")
        self.run_store = run_store
        self.card_store = card_store
        self.ttl_seconds = ttl_seconds
        if resume_ttl_seconds < 1:
            raise ValueError("resume_ttl_seconds must be positive")
        self.resume_ttl_seconds = resume_ttl_seconds
        self._answer_lock = asyncio.Lock()

    async def create(
        self,
        *,
        parent_run_id: str,
        user_id: str,
        ambiguity: MaterialAmbiguity,
    ) -> ClarificationCard:
        parent = await self.run_store.get(parent_run_id)
        if parent is None:
            raise KeyError(parent_run_id)
        if parent.user_id != user_id:
            raise PermissionError("run belongs to another user")
        if len(ambiguity.options) not in {2, 3}:
            raise ValueError("clarification requires two or three options")
        if len({entry.option_id for entry in ambiguity.options}) != len(
            ambiguity.options
        ):
            raise ValueError("clarification option ids must be unique")
        if any(
            not entry.evidence_id or not entry.source_hash
            for entry in ambiguity.options
        ):
            raise ValueError("clarification options must be source-backed")
        if parent.status not in {
            RunStatus.LINKING,
            RunStatus.GENERATING,
            RunStatus.TOOL_RUNNING,
        }:
            raise ValueError(
                "clarification can only be created from linking or schema-search generation"
            )
        card = ClarificationCard(
            card_id=str(uuid.uuid4()),
            parent_run_id=parent_run_id,
            user_id=user_id,
            ambiguity_id=ambiguity.ambiguity_id,
            question=ambiguity.question,
            options=ambiguity.options,
            reason=ambiguity.reason,
            expires_at=utc_now() + timedelta(seconds=self.ttl_seconds),
        )
        await self.card_store.save(card)
        await self.run_store.transition(parent_run_id, RunStatus.NEEDS_CLARIFICATION)
        return card

    async def answer(
        self, parent_run_id: str, user_id: str, option_id: str
    ) -> RunRecord:
        atomic_answer = getattr(self.card_store, "answer_atomic", None)
        if atomic_answer is not None:
            return await atomic_answer(parent_run_id, user_id, option_id)
        async with self._answer_lock:
            card = await self.card_store.get_for_parent(parent_run_id)
            if card is None:
                raise KeyError(parent_run_id)
            if card.user_id != user_id:
                raise PermissionError("clarification belongs to another user")
            if card.answered_at is not None:
                raise ClarificationAlreadyAnswered("clarification already answered")
            if card.expires_at <= utc_now():
                raise ClarificationExpired("clarification has expired")
            selected = next(
                (entry for entry in card.options if entry.option_id == option_id), None
            )
            if selected is None:
                raise ValueError("unknown clarification option")
            parent = await self.run_store.get(parent_run_id)
            if parent is None:
                raise KeyError(parent_run_id)
            digest_input = (
                parent.instruction_hash
                + "\n"
                + selected.evidence_id
                + "\n"
                + selected.source_hash
            )
            child = RunRecord(
                run_id=str(uuid.uuid4()),
                user_id=user_id,
                conversation_id=parent.conversation_id,
                instruction_hash=hashlib.sha256(
                    digest_input.encode("utf-8")
                ).hexdigest(),
                operation_kind=OperationKind.READ_QUERY,
                parent_run_id=parent.run_id,
                correlation_id=parent.correlation_id,
            )
            await self.run_store.create(child)
            card.answered_at = utc_now()
            card.selected_option_id = option_id
            card.child_run_id = child.run_id
            await self.card_store.save(card)
            return child

    async def answer_with_resume(
        self, parent_run_id: str, user_id: str, option_id: str
    ) -> ClarificationResolution:
        parent = await self.run_store.get(parent_run_id)
        if parent is None:
            raise KeyError(parent_run_id)
        if parent.user_id != user_id:
            raise PermissionError("clarification belongs to another user")
        if parent.operation_kind != OperationKind.READ_QUERY:
            raise ValueError("clarification resume only supports READ_QUERY")
        atomic = getattr(self.card_store, "answer_atomic_with_resume", None)
        if atomic is not None:
            return await atomic(
                parent_run_id, user_id, option_id, self.resume_ttl_seconds
            )

        child = await self.answer(parent_run_id, user_id, option_id)
        card = await self.card_store.get_for_parent(parent_run_id)
        assert card is not None
        selected = next(item for item in card.options if item.option_id == option_id)
        token = secrets.token_urlsafe(32)
        expires_at = utc_now() + timedelta(seconds=self.resume_ttl_seconds)
        evidence_hash = hashlib.sha256(
            f"{selected.evidence_id}\n{selected.source_hash}".encode("utf-8")
        ).hexdigest()
        await self.card_store.save_resume(ResumeTokenRecord(
            child_run_id=child.run_id,
            parent_run_id=parent_run_id,
            user_id=user_id,
            token_hash=hashlib.sha256(token.encode("utf-8")).hexdigest(),
            evidence_hash=evidence_hash,
            expires_at=expires_at,
        ))
        return ClarificationResolution(
            child_run_id=child.run_id,
            parent_run_id=parent_run_id,
            resume_token=token,
            expires_at=expires_at,
            selection={
                "option_id": selected.option_id,
                "label": selected.label,
                "evidence_id": selected.evidence_id,
                "source_hash": selected.source_hash,
            },
        )

    async def claim_resume(
        self, child_run_id: str, user_id: str, token: str
    ) -> RunRecord:
        if not token or len(token) > 256:
            raise ResumeInvalid("resume token is invalid")
        child = await self.run_store.get(child_run_id)
        if child is None or child.user_id != user_id:
            raise ResumeNotFound("resume not found")
        if child.operation_kind != OperationKind.READ_QUERY:
            raise ResumeInvalid("resume child must be READ_QUERY")
        if child.status != RunStatus.RECEIVED:
            raise ResumeReplay("resume child already started")
        parent = await self.run_store.get(child.parent_run_id or "")
        if parent is None or parent.status != RunStatus.NEEDS_CLARIFICATION:
            raise ResumeInvalid("resume parent is not awaiting clarification")
        atomic = getattr(self.card_store, "claim_resume_atomic", None)
        if atomic is not None:
            return await atomic(child_run_id, user_id, token)
        await self.card_store.claim_resume(
            child_run_id,
            user_id,
            hashlib.sha256(token.encode("utf-8")).hexdigest(),
        )
        return await self.run_store.transition(child_run_id, RunStatus.CONTEXT_BUILDING)

    async def selected_evidence(self, parent_run_id: str) -> dict[str, str] | None:
        card = await self.card_store.get_for_parent(parent_run_id)
        if card is None or card.selected_option_id is None:
            return None
        selected = next(
            (item for item in card.options if item.option_id == card.selected_option_id),
            None,
        )
        if selected is None:
            return None
        return {
            "option_id": selected.option_id,
            "label": selected.label,
            "evidence_id": selected.evidence_id,
            "source_hash": selected.source_hash,
        }
