from __future__ import annotations

import asyncio
import hashlib
import uuid
from copy import deepcopy
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Protocol

from app.harness.models import OperationKind, RunRecord, RunStatus, utc_now
from app.harness.store import RunStore


class ClarificationAlreadyAnswered(RuntimeError):
    pass


class ClarificationExpired(RuntimeError):
    pass


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


class ClarificationStore(Protocol):
    async def save(self, card: ClarificationCard) -> None: ...
    async def get_for_parent(self, parent_run_id: str) -> ClarificationCard | None: ...


class InMemoryClarificationStore:
    def __init__(self) -> None:
        self._cards: dict[str, ClarificationCard] = {}
        self._lock = asyncio.Lock()

    async def save(self, card: ClarificationCard) -> None:
        async with self._lock:
            self._cards[card.parent_run_id] = deepcopy(card)

    async def get_for_parent(self, parent_run_id: str) -> ClarificationCard | None:
        async with self._lock:
            card = self._cards.get(parent_run_id)
            return deepcopy(card) if card else None


class ClarificationService:
    def __init__(
        self,
        run_store: RunStore,
        card_store: ClarificationStore,
        *,
        ttl_seconds: int = 900,
    ) -> None:
        if ttl_seconds < 1:
            raise ValueError("ttl_seconds must be positive")
        self.run_store = run_store
        self.card_store = card_store
        self.ttl_seconds = ttl_seconds
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
        if len({entry.option_id for entry in ambiguity.options}) != len(ambiguity.options):
            raise ValueError("clarification option ids must be unique")
        if any(not entry.evidence_id or not entry.source_hash for entry in ambiguity.options):
            raise ValueError("clarification options must be source-backed")
        if parent.status != RunStatus.LINKING:
            raise ValueError("clarification can only be created after linking")
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
                instruction_hash=hashlib.sha256(digest_input.encode("utf-8")).hexdigest(),
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

