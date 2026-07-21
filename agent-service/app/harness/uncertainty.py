from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class RiskLevel(StrEnum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    BLOCKED = "BLOCKED"


@dataclass(frozen=True, slots=True)
class UncertaintySignals:
    unresolved_ambiguities: int = 0
    candidate_margin: float | None = None
    source_backed_option_count: int = 0
    unconfirmed_join: bool = False
    evidence_conflicts: int = 0
    memory_conflicts: int = 0
    join_hops: int = 0
    sensitive_or_forbidden: bool = False


@dataclass(frozen=True, slots=True)
class UncertaintyDecision:
    risk_level: RiskLevel
    reasons: tuple[str, ...]
    requires_clarification: bool = False


class UncertaintyGate:
    def evaluate(self, signals: UncertaintySignals) -> UncertaintyDecision:
        reasons: list[str] = []
        if signals.sensitive_or_forbidden:
            return UncertaintyDecision(
                RiskLevel.BLOCKED, ("sensitive_or_forbidden",)
            )
        if signals.unconfirmed_join:
            reasons.append("unconfirmed_join")
            if signals.source_backed_option_count < 2:
                return UncertaintyDecision(RiskLevel.BLOCKED, tuple(reasons))
        conflict_count = signals.evidence_conflicts + signals.memory_conflicts
        if conflict_count:
            reasons.append("conflicting_evidence")
        if signals.unresolved_ambiguities:
            reasons.append("unresolved_ambiguity")
        if signals.candidate_margin is not None and signals.candidate_margin < 0.10:
            reasons.append("narrow_candidate_margin")
        if reasons:
            if signals.source_backed_option_count >= 2:
                return UncertaintyDecision(
                    RiskLevel.HIGH, tuple(reasons), requires_clarification=True
                )
            return UncertaintyDecision(RiskLevel.BLOCKED, tuple(reasons))
        if signals.join_hops >= 4:
            return UncertaintyDecision(RiskLevel.MEDIUM, ("complex_join_path",))
        return UncertaintyDecision(RiskLevel.LOW, ())
