import asyncio

from app.rollout.models import RolloutPolicy
from app.rollout.policy import (
    InMemoryRolloutStore,
    RolloutPolicyService,
    bind_rollout_decision,
    effective_mode,
)


def policy(scope, value, version, percent=100, mode="enforce"):
    return RolloutPolicy(
        policy_id=f"{scope}-{value}-{version}", policy_key=f"{scope}-{value}",
        version=version, scope_type=scope, scope_value=value,
        cohort_percent=percent,
        modes={name: mode for name in ("evidence_drawer", "otel", "clarification_resume", "grounding", "context_harness")},
        created_by="test",
    )


def test_policy_precedence_and_stable_bucket():
    store = InMemoryRolloutStore([
        policy("global", "*", 1), policy("route_risk", "chat:HIGH", 2),
        policy("role", "analyst", 3), policy("tenant", "default", 4),
        policy("user", "alice", 5, percent=10),
    ])
    service = RolloutPolicyService(store)
    first = asyncio.run(service.decide(user_id="alice", tenant_id="default", role="analyst", route="chat", risk="HIGH"))
    second = asyncio.run(service.decide(user_id="alice", tenant_id="default", role="analyst", route="chat", risk="HIGH"))
    assert first.policy_version == 5
    assert first.bucket == second.bucket
    assert first.cohort == second.cohort
    expected = "enforce" if first.bucket < 10 else "shadow"
    assert set(first.modes.values()) == {expected}


def test_request_local_effective_modes_do_not_leak():
    service = RolloutPolicyService(InMemoryRolloutStore([policy("global", "*", 1)]))
    decision = asyncio.run(service.decide(user_id="alice", tenant_id="default", role="analyst", route="chat", risk="LOW"))
    assert effective_mode("grounding", "shadow") == "shadow"
    with bind_rollout_decision(decision):
        assert effective_mode("grounding", "shadow") == "enforce"
    assert effective_mode("grounding", "shadow") == "shadow"


def test_rollback_appends_new_shadow_version_and_preserves_history():
    store = InMemoryRolloutStore([policy("global", "*", 1)])
    service = RolloutPolicyService(store)
    rolled = asyncio.run(service.rollback("global-*-1", actor="admin", reason="drill"))
    assert rolled.version == 2
    assert set(rolled.modes.values()) == {"shadow"}
    assert len(store.policies) == 2
