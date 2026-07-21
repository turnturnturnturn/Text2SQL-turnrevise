from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class RolloutSignals:
    safety_executions: int = 0
    cross_user_leaks: int = 0
    error_count: int = 0
    sample_count: int = 0
    baseline_error_rate: float = 0.0
    simple_p95_seconds: float = 0.0
    complex_p95_seconds: float = 0.0
    p95_breach_minutes: int = 0
    provenance_missing: int = 0
    non_terminal_closure_rate: float = 1.0
    affected_policy_key: str | None = None
    affected_route_risk: str | None = None


class RolloutMonitor:
    def __init__(self, store, service) -> None:
        self.store = store
        self.service = service

    @staticmethod
    def trigger(signals: RolloutSignals) -> str | None:
        if signals.safety_executions or signals.cross_user_leaks:
            return "safety_incident"
        if (
            signals.sample_count >= 20
            and signals.baseline_error_rate > 0
            and signals.error_count / signals.sample_count >= 2 * signals.baseline_error_rate
        ):
            return "error_rate_regression"
        if signals.p95_breach_minutes >= 30 and signals.simple_p95_seconds > 25:
            return "simple_route_p95"
        if signals.p95_breach_minutes >= 30 and signals.complex_p95_seconds > 50:
            return "complex_route_p95"
        if signals.provenance_missing:
            return "provenance_incomplete"
        if signals.non_terminal_closure_rate < 1.0:
            return "terminal_closure_incomplete"
        return None

    async def evaluate_once(self, signals: RolloutSignals):
        reason = self.trigger(signals)
        if reason is None:
            return None
        policies = await self.service.list_policies()
        if reason == "error_rate_regression":
            active = [
                item for item in policies
                if item.active and item.policy_key == signals.affected_policy_key
            ]
        elif reason in {"simple_route_p95", "complex_route_p95"}:
            active = [
                item for item in policies
                if item.active and item.scope_type == "route_risk"
                and item.scope_value == signals.affected_route_risk
            ]
        else:
            active = [item for item in policies if item.active and item.scope_type == "global"]
        if not active:
            return None
        target = max(active, key=lambda item: item.version)
        if target.downgrade_reason == reason:
            return None
        if not await self.store.claim_monitor_version(target.policy_key, target.version):
            return None
        try:
            return await self.service.rollback(
                target.policy_id, actor="phase-d-monitor", reason=reason
            )
        except Exception:
            # A concurrent worker may have won the unique policy-version insert.
            return None
