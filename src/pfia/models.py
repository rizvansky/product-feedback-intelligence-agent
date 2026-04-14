from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class JobStatus(str, Enum):
    """Lifecycle states for asynchronous jobs."""

    queued = "QUEUED"
    running = "RUNNING"
    retrying = "RETRYING"
    degraded_running = "DEGRADED_RUNNING"
    completed = "COMPLETED"
    degraded_completed = "DEGRADED_COMPLETED"
    failed_input = "FAILED_INPUT"
    failed_privacy = "FAILED_PRIVACY"
    failed_runtime = "FAILED_RUNTIME"
    failed_persistence = "FAILED_PERSISTENCE"
    failed_recovery = "FAILED_RECOVERY"
    canceled = "CANCELED"


class JobStage(str, Enum):
    """Pipeline stages tracked for each job."""

    validate_input = "VALIDATE_INPUT"
    preprocess = "PREPROCESS"
    embed = "EMBED"
    cluster = "CLUSTER"
    label_and_summarize = "LABEL_AND_SUMMARIZE"
    score = "SCORE"
    detect_anomalies = "DETECT_ANOMALIES"
    index_for_retrieval = "INDEX_FOR_RETRIEVAL"
    build_report = "BUILD_REPORT"
    finalize = "FINALIZE"


class SessionStatus(str, Enum):
    """User-visible states for an uploaded review session."""

    queued = "QUEUED"
    processing = "PROCESSING"
    completed = "COMPLETED"
    degraded_completed = "DEGRADED_COMPLETED"
    failed = "FAILED"


class ToolName(str, Enum):
    """Tool identifiers exposed by the grounded Q&A layer."""

    top_clusters = "top_clusters"
    search_clusters = "search_clusters"
    get_quotes = "get_quotes"
    get_trend = "get_trend"
    compare_clusters = "compare_clusters"
    get_report_section = "get_report_section"


class ReviewNormalized(BaseModel):
    """Normalized and anonymized review record."""

    model_config = ConfigDict(extra="forbid")

    review_id: str
    session_id: str
    source: str
    created_at: datetime
    rating: int | None = None
    language: str
    app_version: str | None = None
    text_normalized: str
    text_anonymized: str
    dedupe_hash: str
    flags: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class PreprocessingSummary(BaseModel):
    """Aggregate counters emitted by the preprocessing stage."""

    total_records: int
    kept_records: int
    duplicate_records: int
    quarantined_records: int
    pii_hits: int
    injection_hits: int
    low_information_records: int
    unsupported_language_records: int


class ClusterRecord(BaseModel):
    """Serializable representation of a feedback cluster."""

    cluster_id: str
    label: str
    summary: str
    review_ids: list[str]
    top_quote_ids: list[str]
    priority_score: float
    sentiment_score: float
    trend_delta: float
    confidence: str
    degraded_reason: str | None = None
    keywords: list[str] = Field(default_factory=list)
    sources: list[str] = Field(default_factory=list)
    size: int
    anomaly_flag: bool = False


class AlertRecord(BaseModel):
    """Anomaly or informational alert produced by analysis."""

    alert_id: str
    cluster_id: str
    type: str
    severity: str
    reason: str
    spike_ratio: float | None = None
    insufficient_history: bool = False
    created_at: datetime


class ReportArtifact(BaseModel):
    """Rendered report metadata plus Markdown content."""

    report_id: str
    session_id: str
    path: str
    executive_summary: str
    markdown: str
    generated_at: datetime
    degraded_mode: bool


class ToolTrace(BaseModel):
    """Trace entry describing one tool invocation in Q&A."""

    tool: ToolName
    input: dict[str, Any]
    output_summary: str


class ClusterHit(BaseModel):
    """Ranked retrieval hit for a cluster."""

    cluster_id: str
    score: float
    match_reason: str
    label: str
    summary: str
    priority_score: float


class QuoteRecord(BaseModel):
    """Grounding quote selected from a clustered review."""

    review_id: str
    cluster_id: str
    text: str
    source: str
    created_at: datetime


class ReviewPreview(BaseModel):
    """Compact anonymized review payload used by simple-list presentation mode."""

    review_id: str
    source: str
    created_at: datetime
    language: str
    text: str
    flags: list[str] = Field(default_factory=list)
    cluster_id: str | None = None


class TrendSnippet(BaseModel):
    """Trend metadata returned as evidence for a cluster."""

    cluster_id: str
    trend_delta: float
    baseline: float | None = None
    recent_count: int
    note: str


class EvidenceBundle(BaseModel):
    """Grounded evidence assembled for a chat response."""

    query: str
    cluster_hits: list[ClusterHit]
    quotes: list[QuoteRecord]
    trends: list[TrendSnippet]
    context_tokens_estimate: int


class ChatAnswer(BaseModel):
    """Full grounded answer payload returned by the Q&A layer."""

    answer: str
    evidence: EvidenceBundle
    tool_trace: list[ToolTrace]
    degraded_mode: bool = False


class JobRecord(BaseModel):
    """Persisted metadata for one asynchronous job."""

    job_id: str
    session_id: str
    status: JobStatus
    stage: JobStage
    attempt: int = 1
    failure_code: str | None = None
    degraded_mode: bool = False
    message: str | None = None
    created_at: datetime
    updated_at: datetime


class SessionRecord(BaseModel):
    """Persisted metadata for one uploaded dataset session."""

    session_id: str
    status: SessionStatus
    latest_job_id: str
    created_at: datetime
    updated_at: datetime
    degraded_mode: bool = False
    failure_code: str | None = None
    config_snapshot: dict[str, Any] = Field(default_factory=dict)
    report_path: str | None = None
    executive_summary: str | None = None


class SessionRuntimeMetadata(BaseModel):
    """Operational metadata recorded for one completed processing run."""

    runtime_profile: str
    presentation_mode: str = "clustered"
    low_data_mode: bool = False
    trace_correlation_id: str
    trace_exporters_effective: list[str] = Field(default_factory=list)
    trace_local_path: str | None = None
    orchestrator_backend_requested: str
    orchestrator_backend_effective: str
    generation_backend_requested: str
    generation_backend_effective: str
    retrieval_backend_requested: str
    retrieval_backend_effective: str
    pii_backend_requested: str
    pii_backend_effective: str
    sentiment_backend_requested: str
    sentiment_backend_effective: str
    sentiment_model_effective: str | None = None
    embedding_backend: str
    embedding_backend_requested: str | None = None
    embedding_backend_effective: str | None = None
    embedding_model_effective: str | None = None
    openai_generation_enabled: bool
    mistral_fallback_enabled: bool
    anthropic_fallback_enabled: bool
    llm_primary_model: str | None = None
    llm_call_count: int = 0
    embedding_call_count: int = 0
    prompt_tokens_total: int = 0
    completion_tokens_total: int = 0
    embedding_input_tokens_total: int = 0
    estimated_cost_usd: float = 0.0
    provider_usage_summary: dict[str, dict[str, Any]] = Field(default_factory=dict)
    input_filename: str | None = None
    input_content_type: str | None = None
    records_total: int
    records_kept: int
    top_cluster_ids: list[str] = Field(default_factory=list)
    weak_signal_cluster_ids: list[str] = Field(default_factory=list)
    weak_signal_count: int = 0
    mixed_sentiment_cluster_ids: list[str] = Field(default_factory=list)
    mixed_sentiment_cluster_count: int = 0
    mixed_language_review_count: int = 0
    data_dir: str
    embedded_worker: bool
    chroma_persist_dir: str | None = None
    chroma_mode_effective: str | None = None
    chroma_endpoint_effective: str | None = None
    agent_usage: dict[str, dict[str, Any]] = Field(default_factory=dict)


class SessionDetail(BaseModel):
    """Compound session view returned by the API and repository."""

    session: SessionRecord
    job: JobRecord
    preprocessing_summary: PreprocessingSummary | None = None
    clusters: list[ClusterRecord] = Field(default_factory=list)
    top_clusters: list[ClusterRecord] = Field(default_factory=list)
    weak_signals: list[ClusterRecord] = Field(default_factory=list)
    simple_list_reviews: list[ReviewPreview] = Field(default_factory=list)
    presentation_mode: str = "clustered"
    warnings: list[str] = Field(default_factory=list)
    alerts: list[AlertRecord] = Field(default_factory=list)
    report: ReportArtifact | None = None
    runtime_metadata: SessionRuntimeMetadata | None = None


class UploadResponse(BaseModel):
    """API response returned immediately after a file upload."""

    session_id: str
    job_id: str
    status: str


class ChatRequest(BaseModel):
    """Request body for grounded Q&A."""

    question: str = Field(min_length=3, max_length=1000)


class ChatResponse(BaseModel):
    """API response body for grounded Q&A."""

    session_id: str
    question: str
    answer: str
    evidence: EvidenceBundle
    tool_trace: list[ToolTrace]
    degraded_mode: bool
