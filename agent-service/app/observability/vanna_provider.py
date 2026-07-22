from __future__ import annotations

from typing import Any
import logging

from vanna.core.observability import ObservabilityProvider, Span

from app.harness.context import get_run_id as get_harness_run_id
from app.runtime.correlation import current_run_id


logger = logging.getLogger(__name__)


class RedactedObservabilityProvider(ObservabilityProvider):
    """Vanna adapter that forwards metadata only through TraceService's allowlist."""

    def __init__(self, trace_service, metrics=None, otel_bridge=None) -> None:
        self.trace_service = trace_service
        self.metrics = metrics
        self.otel_bridge = otel_bridge

    async def record_metric(self, name, value, unit="", tags=None) -> None:
        # Vanna metric names are intentionally not exposed as unbounded labels.
        del name, value, unit, tags

    async def create_span(
        self, name: str, attributes: dict[str, Any] | None = None
    ) -> Span:
        safe = {
            key: value
            for key, value in (attributes or {}).items()
            if key in self.trace_service.allowed_attributes
        }
        return Span(name=name, attributes=safe)

    async def end_span(self, span: Span) -> None:
        span.end()
        run_id = current_run_id() or get_harness_run_id()
        if run_id is None:
            return
        name = span.name.lower()
        if "guard" in name:
            event_type = "guard_decided"
        elif "tool" in name or "sql" in name or "llm" in name:
            event_type = "sql_generated"
        else:
            event_type = "context_compiled"
        attrs = dict(span.attributes)
        duration = span.duration_ms()
        if duration is not None:
            attrs["duration_ms"] = round(duration, 3)
        stored = await self.trace_service.append(run_id, event_type, attrs)
        if stored is not None and stored.get("sampled") and self.otel_bridge is not None:
            try:
                self.otel_bridge.export_span(span.name, stored)
            except Exception:
                # Export is never authoritative; local persistence already succeeded.
                logger.exception("OTLP export failed")
