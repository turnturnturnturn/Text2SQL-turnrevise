from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone


FEATURES = (
    "evidence_drawer", "otel", "clarification_resume", "grounding", "context_harness"
)


@dataclass(frozen=True, slots=True)
class RolloutPolicy:
    policy_id: str
    policy_key: str
    version: int
    scope_type: str
    scope_value: str
    cohort_percent: int
    modes: dict[str, str]
    created_by: str
    active: bool = True
    downgrade_reason: str | None = None
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def __post_init__(self):
        if self.scope_type not in {"user", "tenant", "role", "route_risk", "global"}:
            raise ValueError("invalid rollout scope")
        if not 0 <= self.cohort_percent <= 100:
            raise ValueError("cohort_percent must be between 0 and 100")
        if set(self.modes) != set(FEATURES):
            raise ValueError("rollout modes must cover every Phase D feature")
        if set(self.modes.values()) - {"off", "shadow", "enforce"}:
            raise ValueError("invalid rollout mode")


@dataclass(frozen=True, slots=True)
class RolloutDecision:
    policy_id: str
    policy_version: int
    policy_key: str
    tenant_id: str
    cohort: str
    bucket: int
    modes: dict[str, str]
    reason: str
