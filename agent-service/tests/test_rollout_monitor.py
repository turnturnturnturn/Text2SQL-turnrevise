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
    async def reason(signals):
        store = InMemoryRolloutStore([policy("global", "*", 1)])
        result = await RolloutMonitor(store, RolloutPolicyService(store)).evaluate_once(signals)
        return result.downgrade_reason if result else None
    assert asyncio.run(reason(RolloutSignals(error_count=20, sample_count=20, baseline_error_rate=.4))) == "error_rate_regression"
    assert asyncio.run(reason(RolloutSignals(simple_p95_seconds=26, p95_breach_minutes=30))) == "simple_route_p95"
    assert asyncio.run(reason(RolloutSignals(provenance_missing=1))) == "provenance_incomplete"
    assert asyncio.run(reason(RolloutSignals(non_terminal_closure_rate=.99))) == "terminal_closure_incomplete"
