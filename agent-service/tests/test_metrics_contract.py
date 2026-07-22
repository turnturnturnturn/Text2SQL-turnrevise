from app.observability.metrics import MetricsRegistry


def test_metrics_exposition_has_required_names_and_no_high_cardinality_labels():
    registry = MetricsRegistry()
    registry.observe_run(1.25, route="chat", risk="LOW", status="COMPLETED", mode="shadow")
    registry.observe_stage(.3, stage="link", model="gpt-5", mode="shadow")
    registry.increment_guard("intent", "allow", "shadow")
    registry.increment_clarification("ambiguous_time", "resumed")
    registry.observe_context_tokens(320, "evidence", "none")
    registry.observe_grounding(.9, "column", "v2")
    registry.increment_rollout("canary", "enforce", "policy")
    text = registry.render()
    for name in (
        "db_copilot_run_duration_seconds",
        "db_copilot_stage_duration_seconds",
        "db_copilot_guard_decisions_total",
        "db_copilot_clarifications_total",
        "db_copilot_context_tokens",
        "db_copilot_grounding_coverage",
        "db_copilot_rollout_decisions_total",
    ):
        assert name in text
    for forbidden in ("user_id=", "run_id=", "prompt=", "sql="):
        assert forbidden not in text.lower()
