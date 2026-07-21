from __future__ import annotations

from datetime import timedelta

import pytest

from app.harness import InMemoryRunStore, RunRecord, RunStatus
from app.harness.clarification import (
    ClarificationAlreadyAnswered,
    ClarificationExpired,
    ClarificationOption,
    ClarificationService,
    InMemoryClarificationStore,
    MaterialAmbiguity,
)
from app.harness.models import utc_now
from app.harness.uncertainty import RiskLevel, UncertaintyGate, UncertaintySignals


def option(option_id: str, evidence_id: str) -> ClarificationOption:
    return ClarificationOption(
        option_id=option_id,
        label=option_id,
        value_id=evidence_id,
        evidence_id=evidence_id,
        source_hash=option_id[0] * 64,
    )


def ambiguity(*options: ClarificationOption) -> MaterialAmbiguity:
    return MaterialAmbiguity(
        ambiguity_id="ambiguity:time-field",
        question="销售时间使用 paid_at 还是 created_at？",
        options=tuple(options),
        reason="two active time definitions",
    )


@pytest.mark.asyncio
async def test_answer_creates_one_child_with_new_hash_and_no_memory_side_effect():
    runs = InMemoryRunStore()
    parent = RunRecord("parent", "alice", "conversation", "a" * 64)
    await runs.create(parent)
    await runs.transition(parent.run_id, RunStatus.CONTEXT_BUILDING)
    await runs.transition(parent.run_id, RunStatus.LINKING)
    cards = InMemoryClarificationStore()
    service = ClarificationService(runs, cards)
    card = await service.create(
        parent_run_id=parent.run_id,
        user_id="alice",
        ambiguity=ambiguity(
            option("paid", "column:orders.paid_at"),
            option("created", "column:orders.created_at"),
        ),
    )

    child = await service.answer(parent.run_id, "alice", card.options[0].option_id)
    assert child.parent_run_id == parent.run_id
    assert child.instruction_hash != parent.instruction_hash
    assert child.correlation_id == parent.correlation_id
    assert child.status == RunStatus.RECEIVED
    with pytest.raises(ClarificationAlreadyAnswered):
        await service.answer(parent.run_id, "alice", card.options[0].option_id)


@pytest.mark.asyncio
async def test_card_requires_two_or_three_source_backed_options():
    runs = InMemoryRunStore()
    parent = RunRecord("parent", "alice", None, "a" * 64)
    await runs.create(parent)
    await runs.transition(parent.run_id, RunStatus.CONTEXT_BUILDING)
    await runs.transition(parent.run_id, RunStatus.LINKING)
    service = ClarificationService(runs, InMemoryClarificationStore())

    with pytest.raises(ValueError, match="two or three"):
        await service.create(
            parent_run_id=parent.run_id,
            user_id="alice",
            ambiguity=ambiguity(option("only", "column:orders.paid_at")),
        )
    missing_source = option("bad", "column:orders.paid_at")
    missing_source = ClarificationOption(
        option_id=missing_source.option_id,
        label=missing_source.label,
        value_id=missing_source.value_id,
        evidence_id=missing_source.evidence_id,
        source_hash="",
    )
    with pytest.raises(ValueError, match="source-backed"):
        await service.create(
            parent_run_id=parent.run_id,
            user_id="alice",
            ambiguity=ambiguity(
                missing_source, option("good", "column:orders.created_at")
            ),
        )


@pytest.mark.asyncio
async def test_cross_user_tamper_and_expired_card_create_no_child():
    runs = InMemoryRunStore()
    parent = RunRecord("parent", "alice", None, "a" * 64)
    await runs.create(parent)
    await runs.transition(parent.run_id, RunStatus.CONTEXT_BUILDING)
    await runs.transition(parent.run_id, RunStatus.LINKING)
    cards = InMemoryClarificationStore()
    service = ClarificationService(runs, cards, ttl_seconds=1)
    card = await service.create(
        parent_run_id=parent.run_id,
        user_id="alice",
        ambiguity=ambiguity(
            option("paid", "column:orders.paid_at"),
            option("created", "column:orders.created_at"),
        ),
    )

    with pytest.raises(PermissionError):
        await service.answer(parent.run_id, "bob", "paid")
    with pytest.raises(ValueError, match="unknown clarification option"):
        await service.answer(parent.run_id, "alice", "forged")
    card.expires_at = utc_now() - timedelta(seconds=1)
    await cards.save(card)
    with pytest.raises(ClarificationExpired):
        await service.answer(parent.run_id, "alice", "paid")
    assert len([record for record in runs._records.values() if record.parent_run_id]) == 0


def test_uncertainty_gate_is_deterministic_and_fail_closed():
    gate = UncertaintyGate()
    assert gate.evaluate(UncertaintySignals()).risk_level == RiskLevel.LOW
    high = gate.evaluate(
        UncertaintySignals(
            unresolved_ambiguities=1,
            candidate_margin=0.02,
            source_backed_option_count=2,
        )
    )
    assert high.risk_level == RiskLevel.HIGH
    assert high.requires_clarification is True
    blocked = gate.evaluate(
        UncertaintySignals(unconfirmed_join=True, source_backed_option_count=1)
    )
    assert blocked.risk_level == RiskLevel.BLOCKED

