from __future__ import annotations

from collections import defaultdict


class MetricsRegistry:
    """Small dependency-free Prometheus text registry with bounded label APIs."""

    def __init__(self) -> None:
        self._values: dict[tuple[str, tuple[tuple[str, str], ...]], list[float]] = defaultdict(list)

    def _record(self, name: str, value: float, **labels: str) -> None:
        self._values[(name, tuple(sorted(labels.items())))].append(float(value))

    def observe_run(self, value, *, route, risk, status, mode):
        self._record("db_copilot_run_duration_seconds", value, route=route, risk=risk, status=status, mode=mode)

    def observe_stage(self, value, *, stage, model, mode):
        self._record("db_copilot_stage_duration_seconds", value, stage=stage, model=model, mode=mode)

    def increment_guard(self, guard_type, decision, mode):
        self._record("db_copilot_guard_decisions_total", 1, guard_type=guard_type, decision=decision, mode=mode)

    def increment_clarification(self, reason, outcome):
        self._record("db_copilot_clarifications_total", 1, reason=reason, outcome=outcome)

    def observe_context_tokens(self, value, partition, prune_reason):
        self._record("db_copilot_context_tokens", value, partition=partition, prune_reason=prune_reason)

    def observe_grounding(self, value, asset_type, version):
        self._record("db_copilot_grounding_coverage", value, asset_type=asset_type, version=version)

    def increment_rollout(self, cohort, decision, reason):
        self._record("db_copilot_rollout_decisions_total", 1, cohort=cohort, decision=decision, reason=reason)

    def render(self) -> str:
        lines = []
        for (name, labels), values in sorted(self._values.items()):
            label_text = ",".join(f'{key}="{value}"' for key, value in labels)
            suffix = f"{{{label_text}}}" if label_text else ""
            lines.append(f"{name}{suffix} {sum(values)}")
        return "\n".join(lines) + "\n"
