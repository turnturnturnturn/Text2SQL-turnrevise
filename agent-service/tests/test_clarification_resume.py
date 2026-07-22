import asyncio
from datetime import timedelta

import pytest

from app.harness import (
    InMemoryRunStore, OperationKind, RequestHarness, RunRecord, RunStatus,
    get_instruction_hash, get_instruction_text,
)
from app.harness.clarification import (
    ClarificationOption,
    ClarificationService,
    InMemoryClarificationStore,
    MaterialAmbiguity,
    ResumeExpired,
    ResumeInvalid,
    ResumeNotFound,
    ResumeReplay,
)
from app.harness.models import utc_now


async def prepared_service(*, kind=OperationKind.READ_QUERY):
    runs = InMemoryRunStore()
    parent = RunRecord("parent", "alice", "conv", "a" * 64, operation_kind=kind)
    await runs.create(parent)
    await runs.transition("parent", RunStatus.CONTEXT_BUILDING)
    await runs.transition("parent", RunStatus.LINKING)
    cards = InMemoryClarificationStore()
    service = ClarificationService(runs, cards, resume_ttl_seconds=300)
    await service.create(
        parent_run_id="parent",
        user_id="alice",
        ambiguity=MaterialAmbiguity(
            "amb", "paid or created?",
            (
                ClarificationOption("paid", "Paid", "value:paid", "column:paid_at", "b" * 64),
                ClarificationOption("created", "Created", "value:created", "column:created_at", "c" * 64),
            ),
            "two fields",
        ),
    )
    return runs, cards, service


@pytest.mark.asyncio
async def test_answer_returns_single_use_plaintext_token_and_stores_only_hash():
    runs, cards, service = await prepared_service()
    resolved = await service.answer_with_resume("parent", "alice", "paid")

    assert resolved.child_run_id != "parent"
    assert resolved.resume_token
    assert resolved.selection == {
        "option_id": "paid", "label": "Paid", "evidence_id": "column:paid_at",
        "source_hash": "b" * 64,
    }
    stored = cards._resume_tokens[resolved.child_run_id]
    assert stored.token_hash != resolved.resume_token
    assert len(stored.token_hash) == 64

    child = await service.claim_resume(resolved.child_run_id, "alice", resolved.resume_token)
    assert child.status == RunStatus.CONTEXT_BUILDING
    with pytest.raises(ResumeReplay):
        await service.claim_resume(resolved.child_run_id, "alice", resolved.resume_token)


@pytest.mark.asyncio
async def test_resume_claim_rejects_tamper_cross_user_expiry_and_concurrency():
    _, cards, service = await prepared_service()
    resolved = await service.answer_with_resume("parent", "alice", "paid")
    with pytest.raises(ResumeNotFound):
        await service.claim_resume(resolved.child_run_id, "bob", resolved.resume_token)
    with pytest.raises(ResumeInvalid):
        await service.claim_resume(resolved.child_run_id, "alice", "forged")
    cards._resume_tokens[resolved.child_run_id].expires_at = utc_now() - timedelta(seconds=1)
    with pytest.raises(ResumeExpired):
        await service.claim_resume(resolved.child_run_id, "alice", resolved.resume_token)

    _, _, second = await prepared_service()
    fresh = await second.answer_with_resume("parent", "alice", "paid")
    results = await asyncio.gather(
        second.claim_resume(fresh.child_run_id, "alice", fresh.resume_token),
        second.claim_resume(fresh.child_run_id, "alice", fresh.resume_token),
        return_exceptions=True,
    )
    assert sum(isinstance(value, RunRecord) for value in results) == 1
    assert sum(isinstance(value, ResumeReplay) for value in results) == 1


@pytest.mark.asyncio
async def test_write_or_approval_child_can_never_resume():
    for kind in (OperationKind.BUSINESS_WRITE, OperationKind.APPROVAL):
        _, _, service = await prepared_service(kind=kind)
        with pytest.raises(ValueError, match="READ_QUERY"):
            await service.answer_with_resume("parent", "alice", "paid")


@pytest.mark.asyncio
async def test_resumed_stream_uses_child_hash_and_fresh_budget():
    runs, _, service = await prepared_service()
    resolved = await service.answer_with_resume("parent", "alice", "paid")
    child = await service.claim_resume(resolved.child_run_id, "alice", resolved.resume_token)
    observed = {}

    async def operation(run):
        observed["hash"] = get_instruction_hash()
        observed["text"] = get_instruction_text()
        observed["tool_count"] = run.store._records[run.run_id].tool_call_count
        yield "ok"

    result = [item async for item in RequestHarness(runs).execute_resumed_stream(
        child=child, instruction="original question", operation=operation
    )]
    assert result == ["ok"]
    assert observed == {
        "hash": child.instruction_hash,
        "text": "original question",
        "tool_count": 0,
    }
    assert (await runs.get(child.run_id)).status == RunStatus.COMPLETED
