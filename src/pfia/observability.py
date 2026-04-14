from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any, Iterator, Protocol

from pfia.metrics import Metrics
from pfia.repository import Repository
from pfia.tracing import CompositeTraceSink, make_trace_record


_CURRENT_OBSERVER: ContextVar[ObservabilityObserver | None] = ContextVar(
    "pfia_observer", default=None
)


class ObservabilityObserver(Protocol):
    """Protocol implemented by active run observers."""

    def on_provider_call(
        self,
        *,
        kind: str,
        provider: str,
        model: str,
        status: str,
        latency_s: float,
        usage: dict[str, Any] | None = None,
        error_code: str | None = None,
    ) -> None:
        """Record one provider call outcome."""

    def on_span(
        self,
        *,
        stage: str,
        event: str,
        level: str,
        message: str,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        """Record one structured span/event."""


@dataclass
class RunTelemetrySnapshot:
    """Aggregated telemetry snapshot for one batch or Q&A flow."""

    correlation_id: str
    llm_call_count: int = 0
    embedding_call_count: int = 0
    prompt_tokens_total: int = 0
    completion_tokens_total: int = 0
    embedding_input_tokens_total: int = 0
    estimated_cost_usd: float = 0.0
    provider_usage_summary: dict[str, dict[str, Any]] = field(default_factory=dict)


class SessionRunObserver:
    """Collect provider telemetry and structured spans for one correlated run."""

    def __init__(
        self,
        *,
        repo: Repository,
        metrics: Metrics,
        trace_sink: CompositeTraceSink,
        session_id: str,
        job_id: str,
        correlation_id: str,
    ) -> None:
        """Bind repository and metrics dependencies to one run observer."""

        self.repo = repo
        self.metrics = metrics
        self.trace_sink = trace_sink
        self.session_id = session_id
        self.job_id = job_id
        self.correlation_id = correlation_id
        self._snapshot = RunTelemetrySnapshot(correlation_id=correlation_id)

    def on_provider_call(
        self,
        *,
        kind: str,
        provider: str,
        model: str,
        status: str,
        latency_s: float,
        usage: dict[str, Any] | None = None,
        error_code: str | None = None,
    ) -> None:
        """Record provider metrics and a structured provider event."""

        normalized_model = model or "unknown"
        usage = usage or {}
        if kind == "llm":
            self.metrics.llm_calls_total.labels(
                provider=provider,
                model=normalized_model,
                operation=kind,
                status=status,
            ).inc()
            self.metrics.provider_latency_seconds.labels(
                provider=provider,
                model=normalized_model,
                operation=kind,
            ).observe(latency_s)
            if status != "success":
                self.metrics.llm_errors_total.labels(
                    provider=provider,
                    error_code=error_code or "UNKNOWN",
                    operation=kind,
                ).inc()
            self._snapshot.llm_call_count += 1
            self._snapshot.prompt_tokens_total += int(
                usage.get("prompt_tokens") or usage.get("input_tokens") or 0
            )
            self._snapshot.completion_tokens_total += int(
                usage.get("completion_tokens") or usage.get("output_tokens") or 0
            )
        elif kind == "embedding":
            self.metrics.embedding_calls_total.labels(
                provider=provider,
                model=normalized_model,
                status=status,
            ).inc()
            self.metrics.provider_latency_seconds.labels(
                provider=provider,
                model=normalized_model,
                operation=kind,
            ).observe(latency_s)
            self._snapshot.embedding_call_count += 1
            self._snapshot.embedding_input_tokens_total += int(
                usage.get("prompt_tokens")
                or usage.get("input_tokens")
                or usage.get("total_tokens")
                or 0
            )

        summary = self._snapshot.provider_usage_summary.setdefault(
            provider,
            {
                "llm_calls": 0,
                "embedding_calls": 0,
                "models": [],
                "last_status": status,
            },
        )
        if kind == "llm":
            summary["llm_calls"] = int(summary["llm_calls"]) + 1
        elif kind == "embedding":
            summary["embedding_calls"] = int(summary["embedding_calls"]) + 1
        if normalized_model not in summary["models"]:
            summary["models"].append(normalized_model)
        summary["last_status"] = status

        self.on_span(
            stage="PROVIDER",
            event=f"provider.{kind}",
            level="INFO" if status == "success" else "ERROR",
            message=(
                f"{provider} {kind} call {status} using {normalized_model} "
                f"in {latency_s:.2f}s."
            ),
            metadata={
                "provider": provider,
                "model": normalized_model,
                "operation": kind,
                "status": status,
                "latency_s": round(latency_s, 4),
                "usage": usage,
                "error_code": error_code,
            },
        )

    def on_span(
        self,
        *,
        stage: str,
        event: str,
        level: str,
        message: str,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        """Persist one structured event into the stage event log."""

        self.repo.log_event(
            self.job_id,
            self.session_id,
            stage,
            event,
            level,
            message,
            correlation_id=self.correlation_id,
            metadata=metadata or {},
        )
        self.trace_sink.emit(
            make_trace_record(
                correlation_id=self.correlation_id,
                session_id=self.session_id,
                job_id=self.job_id,
                stage=stage,
                event=event,
                level=level,
                message=message,
                metadata=metadata or {},
            )
        )

    def snapshot(self) -> RunTelemetrySnapshot:
        """Return a copy-like view of the accumulated telemetry snapshot."""

        return RunTelemetrySnapshot(
            correlation_id=self._snapshot.correlation_id,
            llm_call_count=self._snapshot.llm_call_count,
            embedding_call_count=self._snapshot.embedding_call_count,
            prompt_tokens_total=self._snapshot.prompt_tokens_total,
            completion_tokens_total=self._snapshot.completion_tokens_total,
            embedding_input_tokens_total=self._snapshot.embedding_input_tokens_total,
            estimated_cost_usd=self._snapshot.estimated_cost_usd,
            provider_usage_summary={
                provider: {
                    "llm_calls": values["llm_calls"],
                    "embedding_calls": values["embedding_calls"],
                    "models": list(values["models"]),
                    "last_status": values["last_status"],
                }
                for provider, values in self._snapshot.provider_usage_summary.items()
            },
        )


@contextmanager
def bind_observer(observer: ObservabilityObserver | None) -> Iterator[None]:
    """Bind an observer to the current execution context."""

    token = _CURRENT_OBSERVER.set(observer)
    try:
        yield
    finally:
        _CURRENT_OBSERVER.reset(token)


def get_current_observer() -> ObservabilityObserver | None:
    """Return the currently bound observer, if any."""

    return _CURRENT_OBSERVER.get()


def record_provider_call(
    *,
    kind: str,
    provider: str,
    model: str,
    status: str,
    latency_s: float,
    usage: dict[str, Any] | None = None,
    error_code: str | None = None,
) -> None:
    """Forward one provider call event to the active observer if present."""

    observer = get_current_observer()
    if observer is None:
        return
    observer.on_provider_call(
        kind=kind,
        provider=provider,
        model=model,
        status=status,
        latency_s=latency_s,
        usage=usage,
        error_code=error_code,
    )


def record_span(
    *,
    stage: str,
    event: str,
    level: str,
    message: str,
    metadata: dict[str, Any] | None = None,
) -> None:
    """Forward one structured span event to the active observer if present."""

    observer = get_current_observer()
    if observer is None:
        return
    observer.on_span(
        stage=stage,
        event=event,
        level=level,
        message=message,
        metadata=metadata,
    )
