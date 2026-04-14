from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Protocol

from pfia.config import Settings


@dataclass(frozen=True)
class TraceRecord:
    """Serializable structured trace event."""

    timestamp: str
    correlation_id: str
    session_id: str
    job_id: str
    stage: str
    event: str
    level: str
    message: str
    metadata: dict[str, Any]


class TraceSink(Protocol):
    """Protocol implemented by optional trace exporters."""

    def emit(self, record: TraceRecord) -> None:
        """Emit one trace record to the sink."""


class LocalJsonlTraceSink:
    """Append-only JSONL trace sink stored on the runtime volume."""

    def __init__(self, path: Path) -> None:
        """Persist the output file path for structured traces."""

        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def emit(self, record: TraceRecord) -> None:
        """Append one JSON trace line to the local artifact file."""

        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(asdict(record), ensure_ascii=False))
            handle.write("\n")


class LangSmithTraceSink:
    """Best-effort LangSmith exporter for structured trace records."""

    def __init__(
        self,
        *,
        api_key: str,
        project_name: str,
        endpoint: str,
    ) -> None:
        """Store LangSmith client settings for later lazy initialization."""

        self.api_key = api_key
        self.project_name = project_name
        self.endpoint = endpoint
        self._client = None

    @property
    def available(self) -> bool:
        """Return whether this sink has enough configuration to run."""

        return bool(self.api_key.strip() and self.project_name.strip())

    def emit(self, record: TraceRecord) -> None:
        """Export one structured event to LangSmith on a best-effort basis."""

        if not self.available:
            return
        try:
            if self._client is None:
                from langsmith import Client  # type: ignore

                self._client = Client(api_key=self.api_key, api_url=self.endpoint)
            self._client.create_run(
                name=record.event,
                run_type="chain",
                project_name=self.project_name,
                inputs={
                    "stage": record.stage,
                    "message": record.message,
                    "metadata": record.metadata,
                },
                outputs={"level": record.level},
                extra={
                    "metadata": {
                        "correlation_id": record.correlation_id,
                        "session_id": record.session_id,
                        "job_id": record.job_id,
                    }
                },
            )
        except Exception:
            return


class OTelTraceSink:
    """Best-effort OpenTelemetry span exporter for structured trace records."""

    def __init__(self, endpoint: str) -> None:
        """Store the OTLP endpoint and lazily initialize the tracer."""

        self.endpoint = endpoint
        self._tracer = None

    @property
    def available(self) -> bool:
        """Return whether this sink has enough configuration to run."""

        return bool(self.endpoint.strip())

    def emit(self, record: TraceRecord) -> None:
        """Emit one structured event as a short-lived OpenTelemetry span."""

        if not self.available:
            return
        try:
            if self._tracer is None:
                from opentelemetry import trace  # type: ignore
                from opentelemetry.exporter.otlp.proto.http.trace_exporter import (  # type: ignore
                    OTLPSpanExporter,
                )
                from opentelemetry.sdk.resources import Resource  # type: ignore
                from opentelemetry.sdk.trace import TracerProvider  # type: ignore
                from opentelemetry.sdk.trace.export import (  # type: ignore
                    BatchSpanProcessor,
                )

                provider = TracerProvider(
                    resource=Resource.create({"service.name": "pfia"})
                )
                provider.add_span_processor(
                    BatchSpanProcessor(OTLPSpanExporter(endpoint=self.endpoint))
                )
                trace.set_tracer_provider(provider)
                self._tracer = trace.get_tracer("pfia")
            with self._tracer.start_as_current_span(record.event) as span:
                span.set_attribute("pfia.correlation_id", record.correlation_id)
                span.set_attribute("pfia.session_id", record.session_id)
                span.set_attribute("pfia.job_id", record.job_id)
                span.set_attribute("pfia.stage", record.stage)
                span.set_attribute("pfia.level", record.level)
                span.set_attribute("pfia.message", record.message)
                for key, value in record.metadata.items():
                    span.set_attribute(f"pfia.meta.{key}", str(value))
        except Exception:
            return


class CompositeTraceSink:
    """Fan out trace records to multiple best-effort sinks."""

    def __init__(self, sinks: list[TraceSink], *, effective_names: list[str]) -> None:
        """Store the configured sinks and their effective names."""

        self.sinks = sinks
        self.effective_names = effective_names

    def emit(self, record: TraceRecord) -> None:
        """Emit one trace record to every configured sink."""

        for sink in self.sinks:
            try:
                sink.emit(record)
            except Exception:
                continue


def build_trace_sink(settings: Settings) -> CompositeTraceSink:
    """Build the configured trace sink fan-out for the current runtime."""

    sinks: list[TraceSink] = []
    effective_names: list[str] = []

    local_sink = LocalJsonlTraceSink(settings.traces_dir / "events.jsonl")
    sinks.append(local_sink)
    effective_names.append("local-jsonl")

    if settings.langsmith_tracing:
        langsmith_sink = LangSmithTraceSink(
            api_key=settings.langsmith_api_key,
            project_name=settings.langsmith_project,
            endpoint=settings.langsmith_endpoint,
        )
        if langsmith_sink.available:
            sinks.append(langsmith_sink)
            effective_names.append("langsmith")

    if settings.otel_tracing_enabled and settings.otlp_traces_endpoint.strip():
        sinks.append(OTelTraceSink(settings.otlp_traces_endpoint))
        effective_names.append("otlp")

    return CompositeTraceSink(sinks, effective_names=effective_names)


def make_trace_record(
    *,
    correlation_id: str,
    session_id: str,
    job_id: str,
    stage: str,
    event: str,
    level: str,
    message: str,
    metadata: dict[str, Any] | None = None,
) -> TraceRecord:
    """Construct a trace record with a current UTC timestamp."""

    return TraceRecord(
        timestamp=datetime.now(timezone.utc).isoformat(),
        correlation_id=correlation_id,
        session_id=session_id,
        job_id=job_id,
        stage=stage,
        event=event,
        level=level,
        message=message,
        metadata=metadata or {},
    )
