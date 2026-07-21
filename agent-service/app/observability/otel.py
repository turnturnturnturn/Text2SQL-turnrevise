from __future__ import annotations

import logging


logger = logging.getLogger(__name__)


class OtelBridge:
    """Optional OTLP bridge; imports the SDK only when an endpoint is configured."""

    def __init__(self, endpoint: str | None) -> None:
        self.tracer = None
        if not endpoint:
            return
        try:
            from opentelemetry import trace
            from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
            from opentelemetry.sdk.resources import Resource
            from opentelemetry.sdk.trace import TracerProvider
            from opentelemetry.sdk.trace.export import BatchSpanProcessor

            provider = TracerProvider(resource=Resource.create({
                "service.name": "database-copilot-agent",
                "telemetry.schema.version": "1.0.0",
            }))
            provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter(endpoint=endpoint)))
            trace.set_tracer_provider(provider)
            self.tracer = trace.get_tracer("db_copilot.phase_d", "1.0.0")
        except ImportError:
            logger.warning("OTLP endpoint configured but OpenTelemetry SDK is unavailable")

    def export_span(self, name: str, event: dict) -> None:
        if self.tracer is None:
            return
        with self.tracer.start_as_current_span(name) as span:
            for key, value in event.get("attributes", {}).items():
                span.set_attribute(key, value)
            span.set_attribute("db_copilot.trace_event", event["event_type"])
