import asyncio

from app.rollout.monitor import RolloutMonitor, RolloutSignals
from app.rollout.policy import InMemoryRolloutStore, RolloutPolicyService
from test_rollout_policy import policy


def test_multi_worker_monitor_creates_one_safety_downgrade_version():
    store = InMemoryRolloutStore([policy("global", "*", 1)])
    service = RolloutPolicyService(store)
    monitor_a = RolloutMonitor(store, service)
    monitor_b = RolloutMonitor(store, service)
    signals = RolloutSignals(safety_executions=1)
    async def run_workers():
        return await asyncio.gather(
            monitor_a.evaluate_once(signals), monitor_b.evaluate_once(signals)
        )
    results = asyncio.run(run_workers())
    assert sum(result is not None for result in results) == 1
    assert len(store.policies) == 2
    assert store.policies[-1].downgrade_reason == "safety_incident"


def test_error_latency_and_provenance_triggers_are_scoped():
    async def result_for(signals):
        store = InMemoryRolloutStore([
            policy("global", "*", 1),
            policy("tenant", "default", 1),
            policy("route_risk", "chat:LOW", 1),
        ])
        result = await RolloutMonitor(store, RolloutPolicyService(store)).evaluate_once(signals)
        return result
    error = asyncio.run(result_for(RolloutSignals(
        error_count=20, sample_count=20, baseline_error_rate=.4,
        affected_policy_key="tenant-default",
    )))
    assert error.downgrade_reason == "error_rate_regression"
    assert error.scope_type == "tenant"
    latency = asyncio.run(result_for(RolloutSignals(
        simple_p95_seconds=26, p95_breach_minutes=30,
        affected_route_risk="chat:LOW",
    )))
    assert latency.downgrade_reason == "simple_route_p95"
    assert latency.scope_type == "route_risk"
    provenance = asyncio.run(result_for(RolloutSignals(provenance_missing=1)))
    assert provenance.downgrade_reason == "provenance_incomplete"
    assert provenance.scope_type == "global"
    closure = asyncio.run(result_for(RolloutSignals(non_terminal_closure_rate=.99)))
    assert closure.downgrade_reason == "terminal_closure_incomplete"
    assert closure.scope_type == "global"


def test_route_breach_without_matching_policy_does_not_expand_to_global():
    store = InMemoryRolloutStore([policy("global", "*", 1)])
    result = asyncio.run(RolloutMonitor(store, RolloutPolicyService(store)).evaluate_once(
        RolloutSignals(simple_p95_seconds=26, p95_breach_minutes=30,
                       affected_route_risk="chat:LOW")
    ))
    assert result is None
