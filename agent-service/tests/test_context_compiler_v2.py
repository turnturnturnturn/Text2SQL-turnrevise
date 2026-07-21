from __future__ import annotations

import pytest

from app.context_v2 import (
    ContextCompilationError,
    ContextCompiler,
    ContextItem,
    ContextPartition,
)
from app.context_v2.store import InMemoryContextStore


def item(
    item_id: str,
    partition: ContextPartition,
    content: str,
    *,
    mandatory: bool = False,
    priority: int = 50,
    source_id: str | None = None,
    source_hash: str | None = None,
) -> ContextItem:
    return ContextItem(
        item_id=item_id,
        partition=partition,
        content=content,
        source_id=f"source:{item_id}" if source_id is None else source_id,
        source_hash=(item_id[0] * 64) if source_hash is None else source_hash,
        trust_level="verified",
        priority=priority,
        mandatory=mandatory,
    )


def test_compiler_never_prunes_mandatory_layers_and_records_optional_pruning():
    compiled = ContextCompiler(token_estimator=len).compile(
        run_id="r1",
        total_token_budget=90,
        mode="enforce",
        items=[
            item("safety", ContextPartition.SAFETY, "safe", mandatory=True),
            item("request", ContextPartition.REQUEST, "question", mandatory=True),
            item("old", ContextPartition.CONVERSATION, "x" * 100, priority=1),
        ],
    )

    assert [entry.item_id for entry in compiled.manifest.included_items] == [
        "safety",
        "request",
    ]
    assert compiled.manifest.pruned_items[0].item_id == "old"
    assert compiled.manifest.pruned_items[0].reason == "partition_budget_exceeded"
    assert compiled.manifest.provenance_coverage == 1.0


def test_compiler_orders_sections_by_irreversible_policy_priority():
    compiled = ContextCompiler(token_estimator=len).compile(
        run_id="r2",
        total_token_budget=1000,
        mode="enforce",
        items=[
            item("memory", ContextPartition.MEMORY, "remembered"),
            item("request", ContextPartition.REQUEST, "current", mandatory=True),
            item("safety", ContextPartition.SAFETY, "policy", mandatory=True),
            item("schema", ContextPartition.GROUNDING, "orders.status"),
        ],
    )

    assert [section.partition for section in compiled.sections] == [
        ContextPartition.SAFETY,
        ContextPartition.REQUEST,
        ContextPartition.GROUNDING,
        ContextPartition.MEMORY,
    ]


def test_enforce_rejects_mandatory_item_without_provenance():
    with pytest.raises(ContextCompilationError, match="mandatory context item"):
        ContextCompiler(token_estimator=len).compile(
            run_id="r3",
            total_token_budget=100,
            mode="enforce",
            items=[
                item(
                    "safety",
                    ContextPartition.SAFETY,
                    "policy",
                    mandatory=True,
                    source_id="",
                )
            ],
        )


def test_shadow_records_missing_optional_provenance_without_including_content():
    compiled = ContextCompiler(token_estimator=len).compile(
        run_id="r4",
        total_token_budget=100,
        mode="shadow",
        items=[
            item("safety", ContextPartition.SAFETY, "policy", mandatory=True),
            item(
                "optional",
                ContextPartition.EXAMPLE,
                "untrusted",
                source_hash="",
            ),
        ],
    )

    assert "untrusted" not in compiled.prompt
    assert compiled.manifest.pruned_items[0].reason == "missing_provenance"
    redacted = compiled.manifest.to_redacted_dict()
    assert "content" not in str(redacted)


def test_enforce_fails_when_mandatory_items_exceed_total_budget():
    with pytest.raises(ContextCompilationError, match="mandatory context exceeds"):
        ContextCompiler(token_estimator=len).compile(
            run_id="r5",
            total_token_budget=5,
            mode="enforce",
            items=[item("safety", ContextPartition.SAFETY, "too-long", mandatory=True)],
        )


@pytest.mark.asyncio
async def test_store_keeps_every_model_call_manifest_version():
    store = InMemoryContextStore()
    compiler = ContextCompiler(token_estimator=len)
    first = compiler.compile(
        run_id="versioned",
        total_token_budget=100,
        mode="shadow",
        items=[item("safety", ContextPartition.SAFETY, "one", mandatory=True)],
    ).manifest
    second = compiler.compile(
        run_id="versioned",
        total_token_budget=100,
        mode="shadow",
        items=[item("safety", ContextPartition.SAFETY, "second", mandatory=True)],
    ).manifest
    await store.save_manifest(first)
    await store.save_manifest(second)

    versions = await store.list_manifests("versioned")
    assert [entry["sequence_no"] for entry in versions] == [0, 1]
    assert (await store.get_manifest("versioned"))["actual_tokens"] == len("second")


def test_optional_context_cannot_crowd_out_later_mandatory_context():
    compiled = ContextCompiler(
        token_estimator=len,
        partition_ratios={partition: 1.0 for partition in ContextPartition},
    ).compile(
        run_id="mandatory-first",
        total_token_budget=10,
        mode="enforce",
        items=[
            item("optional-safety", ContextPartition.SAFETY, "123456"),
            item(
                "mandatory-request", ContextPartition.REQUEST, "abcdef", mandatory=True
            ),
        ],
    )
    assert compiled.manifest.actual_tokens == 6
    assert [entry.item_id for entry in compiled.manifest.included_items] == [
        "mandatory-request"
    ]
