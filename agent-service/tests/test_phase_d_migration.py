from pathlib import Path


MIGRATION = Path(__file__).parents[2] / "database/migrations/006_phase_d_observability_rollout.sql"


def test_phase_d_migration_has_all_private_idempotent_tables():
    sql = MIGRATION.read_text()
    tables = (
        "run_trace_events",
        "clarification_resume_tokens",
        "rollout_policies",
        "rollout_decisions",
        "evaluation_releases",
        "evaluation_cases",
        "evaluation_results",
    )
    for table in tables:
        assert f"CREATE TABLE IF NOT EXISTS agent_state.{table}" in sql
        assert table in sql.split("TO copilot_agent_state", 1)[0]
        assert table in sql.split("FROM copilot_readonly", 1)[0]
    token_table = sql.split(
        "CREATE TABLE IF NOT EXISTS agent_state.clarification_resume_tokens", 1
    )[1].split(");", 1)[0]
    assert "resume_token " not in token_table
    assert "token_hash CHAR(64)" in sql
    assert "prompt" not in sql.lower()
    assert "result_rows" not in sql.lower()


def test_phase_d_init_wrapper_executes_migration():
    wrapper = Path(__file__).parents[2] / "database/init/006_apply_phase_d_observability_rollout.sh"
    assert "006_phase_d_observability_rollout.sql" in wrapper.read_text()
