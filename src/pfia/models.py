from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class JobStatus(str, Enum):
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
    queued = "QUEUED"
    processing = "PROCESSING"
    completed = "COMPLETED"
    degraded_completed = "DEGRADED_COMPLETED"
    failed = "FAILED"


class ToolName(str, Enum):
    search_clusters = "search_clusters"
    get_quotes = "get_quotes"
    get_trend = "get_trend"
    compare_clusters = "compare_clusters"
    get_report_section = "get_report_section"


class ReviewNormalized(BaseModel):
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
    total_records: int
    kept_records: int
    duplicate_records: int
    quarantined_records: int
    pii_hits: int
    injection_hits: int
    low_information_records: int
    unsupported_language_records: int


class ClusterRecord(BaseModel):
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
    alert_id: str
    cluster_id: str
    type: str
    severity: str
    reason: str
    spike_ratio: float | None = None
    insufficient_history: bool = False
    created_at: datetime


class ReportArtifact(BaseModel):
    report_id: str
    session_id: str
    path: str
    executive_summary: str
    markdown: str
    generated_at: datetime
    degraded_mode: bool


class ToolTrace(BaseModel):
    tool: ToolName
    input: dict[str, Any]
    output_summary: str


class ClusterHit(BaseModel):
    cluster_id: str
    score: float
    match_reason: str
    label: str
    summary: str
    priority_score: float


class QuoteRecord(BaseModel):
    review_id: str
    cluster_id: str
    text: str
    source: str
    created_at: datetime


class TrendSnippet(BaseModel):
    cluster_id: str
    trend_delta: float
    baseline: float | None = None
    recent_count: int
    note: str


class EvidenceBundle(BaseModel):
    query: str
    cluster_hits: list[ClusterHit]
    quotes: list[QuoteRecord]
    trends: list[TrendSnippet]
    context_tokens_estimate: int


class ChatAnswer(BaseModel):
    answer: str
    evidence: EvidenceBundle
    tool_trace: list[ToolTrace]
    degraded_mode: bool = False


class JobRecord(BaseModel):
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


class SessionDetail(BaseModel):
    session: SessionRecord
    job: JobRecord
    preprocessing_summary: PreprocessingSummary | None = None
    clusters: list[ClusterRecord] = Field(default_factory=list)
    alerts: list[AlertRecord] = Field(default_factory=list)
    report: ReportArtifact | None = None


class UploadResponse(BaseModel):
    session_id: str
    job_id: str
    status: str


class ChatRequest(BaseModel):
    question: str = Field(min_length=3, max_length=1000)


class ChatResponse(BaseModel):
    session_id: str
    question: str
    answer: str
    evidence: EvidenceBundle
    tool_trace: list[ToolTrace]
    degraded_mode: bool
