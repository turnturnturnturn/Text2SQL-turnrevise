import asyncio
import json

import pytest

from app.observability.trace import (
    InMemoryTraceStore,
    TracePersistenceError,
    TraceService,
)
from app.observability.vanna_provider import RedactedObservabilityProvider
from app.runtime.correlation import bind_run_identity


def test_trace_allowlist_removes_sensitive_content_and_orders_events():
    store = InMemoryTraceStore()
    service = TraceService(store, mode="shadow", success_sample_rate=0.05)
    async def record():
        await asyncio.gather(*[
            service.append("run-1", "context_compiled", {
                "tenant_id": "default", "duration_ms": i,
                "prompt": "secret", "sql": "SELECT private", "user_id": "alice",
                "db_copilot.context_tokens": i,
            }) for i in range(8)
        ])
        return await service.list_events("run-1")
    events = asyncio.run(record())
    assert [event["sequence_no"] for event in events] == list(range(1, 9))
    payload = json.dumps(events)
    assert "secret" not in payload and "SELECT private" not in payload and "alice" not in payload
    assert all(set(event["attributes"]) <= service.allowed_attributes for event in events)


def test_trace_unknown_event_is_rejected_and_enforce_local_failure_closes():
    service = TraceService(InMemoryTraceStore(), mode="shadow")
    with pytest.raises(ValueError, match="event type"):
        asyncio.run(service.append("run", "model_thought", {}))

    class Broken:
        async def append(self, *_args, **_kwargs): raise OSError("disk full")
    asyncio.run(TraceService(Broken(), mode="shadow").append("run", "request_received", {}))
    with pytest.raises(TracePersistenceError):
        asyncio.run(TraceService(Broken(), mode="enforce").append("run", "request_received", {}))


def test_sampling_is_stable_and_failures_are_always_sampled():
    service = TraceService(InMemoryTraceStore(), success_sample_rate=0.05)
    assert service.should_sample("stable-run", "COMPLETED") == service.should_sample("stable-run", "COMPLETED")
    assert service.should_sample("stable-run", "FAILED") is True
    assert service.should_sample("stable-run", "NEEDS_CLARIFICATION") is True


def test_vanna_provider_drops_content_attributes_before_recording():
    store = InMemoryTraceStore()
    provider = RedactedObservabilityProvider(TraceService(store), metrics=None)
    async def exercise():
        with bind_run_identity("run-vanna", "run-vanna"):
            span = await provider.create_span("llm.generate", {
                "model_id": "gpt-5", "gen_ai.input.messages": "secret",
                "sql": "SELECT private",
            })
            await provider.end_span(span)
        return await store.list_events("run-vanna")
    events = asyncio.run(exercise())
    assert events[0]["event_type"] == "sql_generated"
    assert events[0]["attributes"]["model_id"] == "gpt-5"
    assert "duration_ms" in events[0]["attributes"]
    assert "gen_ai.input.messages" not in events[0]["attributes"]
