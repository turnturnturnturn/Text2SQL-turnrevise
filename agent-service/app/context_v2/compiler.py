from __future__ import annotations

from collections.abc import Callable, Iterable

from app.context_v2.models import (
    PARTITION_ORDER,
    CompiledContext,
    ContextItem,
    ContextManifest,
    ContextPartition,
    ContextSection,
    ManifestItem,
    PrunedContextItem,
)


class ContextCompilationError(RuntimeError):
    pass


DEFAULT_PARTITION_RATIOS: dict[ContextPartition, float] = {
    ContextPartition.SAFETY: 0.10,
    ContextPartition.AUTH: 0.05,
    ContextPartition.REQUEST: 0.10,
    ContextPartition.PLAN: 0.05,
    ContextPartition.GROUNDING: 0.30,
    ContextPartition.EVIDENCE: 0.20,
    ContextPartition.CONVERSATION: 0.06,
    ContextPartition.SUMMARY: 0.04,
    ContextPartition.MEMORY: 0.05,
    ContextPartition.EXAMPLE: 0.05,
}


def estimate_tokens(content: str) -> int:
    return max(1, (len(content) + 3) // 4)


class ContextCompiler:
    def __init__(
        self,
        *,
        policy_version: str = "context-v2.0",
        token_estimator: Callable[[str], int] = estimate_tokens,
        partition_ratios: dict[ContextPartition, float] | None = None,
    ) -> None:
        self.policy_version = policy_version
        self.token_estimator = token_estimator
        self.partition_ratios = partition_ratios or DEFAULT_PARTITION_RATIOS

    def compile(
        self,
        *,
        run_id: str,
        items: Iterable[ContextItem],
        total_token_budget: int,
        mode: str = "shadow",
    ) -> CompiledContext:
        if mode not in {"off", "shadow", "enforce"}:
            raise ValueError("mode must be off, shadow, or enforce")
        if total_token_budget < 1:
            raise ValueError("total_token_budget must be positive")

        indexed = {partition: index for index, partition in enumerate(PARTITION_ORDER)}
        ordered = sorted(
            list(items),
            key=lambda entry: (
                indexed[entry.partition],
                -entry.priority,
                entry.item_id,
            ),
        )
        partition_budgets = {
            partition.value: int(total_token_budget * self.partition_ratios[partition])
            for partition in PARTITION_ORDER
        }
        partition_usage = {partition.value: 0 for partition in PARTITION_ORDER}
        included: list[tuple[ContextItem, int]] = []
        pruned: list[PrunedContextItem] = []

        mandatory_tokens = sum(
            self.token_estimator(entry.content)
            for entry in ordered
            if entry.mandatory and entry.has_provenance and not entry.is_expired
        )
        if mandatory_tokens > total_token_budget and mode == "enforce":
            raise ContextCompilationError(
                "mandatory context exceeds total token budget"
            )

        total_used = 0
        valid: list[tuple[ContextItem, int]] = []
        for entry in ordered:
            tokens = self.token_estimator(entry.content)
            if not entry.has_provenance:
                if entry.mandatory and mode == "enforce":
                    raise ContextCompilationError(
                        f"mandatory context item lacks provenance: {entry.item_id}"
                    )
                pruned.append(
                    PrunedContextItem(
                        entry.item_id, entry.partition, "missing_provenance", tokens
                    )
                )
                continue
            if entry.is_expired:
                pruned.append(
                    PrunedContextItem(entry.item_id, entry.partition, "expired", tokens)
                )
                continue
            valid.append((entry, tokens))

        # Reserve the total budget for mandatory policy/request items before
        # admitting optional evidence. Otherwise an early optional partition
        # can crowd out a later mandatory item and make actual_tokens overflow.
        admission_order = [pair for pair in valid if pair[0].mandatory]
        admission_order.extend(pair for pair in valid if not pair[0].mandatory)
        for entry, tokens in admission_order:
            partition_key = entry.partition.value
            exceeds_partition = (
                partition_usage[partition_key] + tokens
                > partition_budgets[partition_key]
            )
            exceeds_total = total_used + tokens > total_token_budget
            if not entry.mandatory and (exceeds_partition or exceeds_total):
                reason = (
                    "partition_budget_exceeded"
                    if exceeds_partition
                    else "total_budget_exceeded"
                )
                pruned.append(
                    PrunedContextItem(entry.item_id, entry.partition, reason, tokens)
                )
                continue
            included.append((entry, tokens))
            partition_usage[partition_key] += tokens
            total_used += tokens

        included.sort(
            key=lambda pair: (
                indexed[pair[0].partition],
                -pair[0].priority,
                pair[0].item_id,
            )
        )
        if total_used > total_token_budget and mode == "enforce":
            raise ContextCompilationError("compiled context exceeds total token budget")

        sections: list[ContextSection] = []
        for partition in PARTITION_ORDER:
            entries = [entry for entry, _ in included if entry.partition == partition]
            if not entries:
                continue
            sections.append(
                ContextSection(
                    partition=partition,
                    content="\n".join(entry.content for entry in entries),
                    item_ids=tuple(entry.item_id for entry in entries),
                )
            )

        manifest_items = tuple(
            ManifestItem(
                item_id=entry.item_id,
                partition=entry.partition,
                source_id=entry.source_id,
                source_hash=entry.source_hash,
                trust_level=entry.trust_level,
                token_count=tokens,
                mandatory=entry.mandatory,
            )
            for entry, tokens in included
        )
        return CompiledContext(
            sections=tuple(sections),
            manifest=ContextManifest(
                run_id=run_id,
                policy_version=self.policy_version,
                mode=mode,
                total_token_budget=total_token_budget,
                actual_tokens=total_used,
                partition_budgets=partition_budgets,
                partition_usage=partition_usage,
                included_items=manifest_items,
                pruned_items=tuple(pruned),
            ),
        )
