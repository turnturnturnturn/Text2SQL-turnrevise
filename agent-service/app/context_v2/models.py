from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import StrEnum
from typing import Any


class ContextPartition(StrEnum):
    SAFETY = "safety"
    AUTH = "auth"
    REQUEST = "request"
    PLAN = "plan"
    EVIDENCE = "evidence"
    GROUNDING = "grounding"
    MEMORY = "memory"
    CONVERSATION = "conversation"
    SUMMARY = "summary"
    EXAMPLE = "example"


PARTITION_ORDER = (
    ContextPartition.SAFETY,
    ContextPartition.AUTH,
    ContextPartition.REQUEST,
    ContextPartition.PLAN,
    ContextPartition.EVIDENCE,
    ContextPartition.GROUNDING,
    ContextPartition.MEMORY,
    ContextPartition.CONVERSATION,
    ContextPartition.SUMMARY,
    ContextPartition.EXAMPLE,
)


@dataclass(frozen=True, slots=True)
class ContextItem:
    item_id: str
    partition: ContextPartition
    content: str
    source_id: str
    source_hash: str
    trust_level: str
    priority: int = 50
    mandatory: bool = False
    expires_at: datetime | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def has_provenance(self) -> bool:
        return bool(self.source_id.strip() and self.source_hash.strip())

    @property
    def is_expired(self) -> bool:
        if self.expires_at is None:
            return False
        now = datetime.now(timezone.utc)
        expiry = self.expires_at
        if expiry.tzinfo is None:
            expiry = expiry.replace(tzinfo=timezone.utc)
        return expiry <= now


@dataclass(frozen=True, slots=True)
class ManifestItem:
    item_id: str
    partition: ContextPartition
    source_id: str
    source_hash: str
    trust_level: str
    token_count: int
    mandatory: bool


@dataclass(frozen=True, slots=True)
class PrunedContextItem:
    item_id: str
    partition: ContextPartition
    reason: str
    estimated_tokens: int


@dataclass(frozen=True, slots=True)
class ContextSection:
    partition: ContextPartition
    content: str
    item_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ContextManifest:
    run_id: str
    policy_version: str
    mode: str
    total_token_budget: int
    actual_tokens: int
    partition_budgets: dict[str, int]
    partition_usage: dict[str, int]
    included_items: tuple[ManifestItem, ...]
    pruned_items: tuple[PrunedContextItem, ...]
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    @property
    def provenance_coverage(self) -> float | None:
        if not self.included_items:
            return None
        covered = sum(
            bool(entry.source_id and entry.source_hash) for entry in self.included_items
        )
        return covered / len(self.included_items)

    def to_redacted_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "policy_version": self.policy_version,
            "mode": self.mode,
            "total_token_budget": self.total_token_budget,
            "actual_tokens": self.actual_tokens,
            "partition_budgets": dict(self.partition_budgets),
            "partition_usage": dict(self.partition_usage),
            "provenance_coverage": self.provenance_coverage,
            "included_items": [
                {
                    "item_id": entry.item_id,
                    "partition": entry.partition.value,
                    "source_id": entry.source_id,
                    "source_hash": entry.source_hash,
                    "trust_level": entry.trust_level,
                    "token_count": entry.token_count,
                    "mandatory": entry.mandatory,
                }
                for entry in self.included_items
            ],
            "pruned_items": [
                {
                    "item_id": entry.item_id,
                    "partition": entry.partition.value,
                    "reason": entry.reason,
                    "estimated_tokens": entry.estimated_tokens,
                }
                for entry in self.pruned_items
            ],
            "created_at": self.created_at.isoformat(),
        }


@dataclass(frozen=True, slots=True)
class CompiledContext:
    sections: tuple[ContextSection, ...]
    manifest: ContextManifest

    @property
    def prompt(self) -> str:
        return "\n\n".join(
            f"<context partition=\"{section.partition.value}\">\n"
            f"{section.content}\n</context>"
            for section in self.sections
        )

