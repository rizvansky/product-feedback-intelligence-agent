from __future__ import annotations

from prometheus_client import (
    CollectorRegistry,
    Counter,
    Gauge,
    Histogram,
    generate_latest,
)


class Metrics:
    """Owns the in-process Prometheus registry for PFIA."""

    def __init__(self) -> None:
        """Initialize counters, gauges, and histograms used by the PoC."""
        self.registry = CollectorRegistry()
        self.job_total = Counter(
            "pfia_job_total",
            "Count of jobs by status.",
            labelnames=("status",),
            registry=self.registry,
        )
        self.job_latency_seconds = Histogram(
            "pfia_job_latency_seconds",
            "Latency of batch jobs.",
            labelnames=("status",),
            registry=self.registry,
            buckets=(1, 3, 5, 10, 20, 30, 45, 60, 120),
        )
        self.qna_latency_seconds = Histogram(
            "pfia_qna_latency_seconds",
            "Latency of grounded Q&A requests.",
            registry=self.registry,
            buckets=(0.1, 0.25, 0.5, 1, 2, 4, 8, 12),
        )
        self.llm_calls_total = Counter(
            "pfia_llm_calls_total",
            "Count of LLM provider calls by provider, model, operation, and status.",
            labelnames=("provider", "model", "operation", "status"),
            registry=self.registry,
        )
        self.llm_errors_total = Counter(
            "pfia_llm_errors_total",
            "Count of LLM provider errors by provider, operation, and error code.",
            labelnames=("provider", "error_code", "operation"),
            registry=self.registry,
        )
        self.embedding_calls_total = Counter(
            "pfia_embedding_calls_total",
            "Count of embedding provider calls by provider, model, and status.",
            labelnames=("provider", "model", "status"),
            registry=self.registry,
        )
        self.provider_latency_seconds = Histogram(
            "pfia_provider_latency_seconds",
            "Latency of provider calls by provider, model, and operation.",
            labelnames=("provider", "model", "operation"),
            registry=self.registry,
            buckets=(0.05, 0.1, 0.25, 0.5, 1, 2, 4, 8, 16, 32),
        )
        self.stage_retries_total = Counter(
            "pfia_stage_retries_total",
            "Stage retries.",
            labelnames=("stage",),
            registry=self.registry,
        )
        self.degraded_jobs_total = Counter(
            "pfia_degraded_jobs_total",
            "Jobs completed in degraded mode.",
            registry=self.registry,
        )
        self.pii_quarantine_total = Counter(
            "pfia_pii_quarantine_total",
            "Reviews moved to quarantine because of unresolved PII.",
            registry=self.registry,
        )
        self.injection_detected_total = Counter(
            "pfia_injection_detected_total",
            "Potential injection attempts seen during preprocessing.",
            registry=self.registry,
        )
        self.cost_usd_total = Counter(
            "pfia_cost_usd_total",
            "Estimated cumulative cost in USD.",
            registry=self.registry,
        )
        self.session_cost_usd = Gauge(
            "pfia_session_cost_usd",
            "Estimated cost in USD for the latest completed session.",
            labelnames=("session_id",),
            registry=self.registry,
        )
        self.queue_depth = Gauge(
            "pfia_queue_depth",
            "Current queue depth.",
            registry=self.registry,
        )

    def render(self) -> bytes:
        """Render the registry in Prometheus text exposition format.

        Returns:
            Serialized metrics payload ready for the HTTP response body.
        """
        return generate_latest(self.registry)
