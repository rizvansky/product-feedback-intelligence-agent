from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from typing import Any

from pfia.db import Database, utcnow
from pfia.models import (
    AlertRecord,
    ClusterRecord,
    JobRecord,
    JobStage,
    JobStatus,
    PreprocessingSummary,
    QuoteRecord,
    ReportArtifact,
    ReviewPreview,
    ReviewNormalized,
    SessionDetail,
    SessionRuntimeMetadata,
    SessionRecord,
    SessionStatus,
)


def _json_dumps(value: Any) -> str:
    """Serialize a Python value to UTF-8-safe JSON text."""
    return json.dumps(value, ensure_ascii=False)


def _json_loads(value: str | None, default: Any) -> Any:
    """Deserialize JSON text or return a default when empty."""
    if not value:
        return default
    return json.loads(value)


def _parse_dt(value: str) -> datetime:
    """Parse an ISO timestamp and ensure it is timezone-aware."""
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed


class Repository:
    """Persistence adapter over the SQLite-backed PFIA schema."""

    def __init__(self, db: Database):
        """Store the low-level database dependency.

        Args:
            db: Initialized database adapter.
        """
        self.db = db

    def create_session_and_job(
        self,
        session_id: str,
        job_id: str,
        config_snapshot: dict[str, Any],
        *,
        session_status: SessionStatus = SessionStatus.queued,
        job_status: JobStatus = JobStatus.queued,
        stage: JobStage = JobStage.validate_input,
    ) -> None:
        """Create the initial session row, job row, and accepted-upload event."""
        timestamp = utcnow().isoformat()
        with self.db.connection() as connection:
            connection.execute(
                """
                INSERT INTO sessions (
                    session_id, status, latest_job_id, created_at, updated_at,
                    degraded_mode, failure_code, config_snapshot_json, report_path, executive_summary
                ) VALUES (?, ?, ?, ?, ?, 0, NULL, ?, NULL, NULL)
                """,
                (
                    session_id,
                    session_status.value,
                    job_id,
                    timestamp,
                    timestamp,
                    _json_dumps(config_snapshot),
                ),
            )
            connection.execute(
                """
                INSERT INTO jobs (
                    job_id, session_id, status, stage, attempt, failure_code,
                    degraded_mode, message, created_at, updated_at
                ) VALUES (?, ?, ?, ?, 1, NULL, 0, ?, ?, ?)
                """,
                (
                    job_id,
                    session_id,
                    job_status.value,
                    stage.value,
                    "Upload accepted",
                    timestamp,
                    timestamp,
                ),
            )
        self.log_event(
            job_id, session_id, stage, "upload.accepted", "INFO", "Upload accepted"
        )

    def log_event(
        self,
        job_id: str,
        session_id: str,
        stage: JobStage | str,
        event: str,
        level: str,
        message: str,
        *,
        correlation_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        """Append a stage event to the audit log."""
        resolved_correlation_id = correlation_id
        if resolved_correlation_id is None:
            try:  # lazy import to avoid a module cycle at import time
                from pfia.observability import get_current_observer

                observer = get_current_observer()
                resolved_correlation_id = getattr(observer, "correlation_id", None)
            except Exception:
                resolved_correlation_id = None
        self.db.execute(
            """
            INSERT INTO job_events (
                job_id, session_id, stage, event, level, message, correlation_id, metadata_json, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                job_id,
                session_id,
                stage.value if isinstance(stage, JobStage) else stage,
                event,
                level,
                message,
                resolved_correlation_id or job_id,
                _json_dumps(metadata or {}),
                utcnow().isoformat(),
            ),
        )

    def get_queue_depth(self) -> int:
        """Return the number of jobs occupying the active queue."""
        row = self.db.fetchone(
            """
            SELECT COUNT(*) AS total
            FROM jobs
            WHERE status IN (?, ?, ?, ?)
            """,
            (
                JobStatus.queued.value,
                JobStatus.running.value,
                JobStatus.retrying.value,
                JobStatus.degraded_running.value,
            ),
        )
        return int(row["total"]) if row else 0

    def get_next_queued_job_id(self) -> str | None:
        """Return the oldest queued job id, if one exists."""
        row = self.db.fetchone(
            """
            SELECT job_id
            FROM jobs
            WHERE status = ?
            ORDER BY created_at ASC
            LIMIT 1
            """,
            (JobStatus.queued.value,),
        )
        if row is None:
            return None
        return str(row["job_id"])

    def set_job_state(
        self,
        job_id: str,
        *,
        status: JobStatus | None = None,
        stage: JobStage | None = None,
        attempt: int | None = None,
        failure_code: str | None = None,
        degraded_mode: bool | None = None,
        message: str | None = None,
    ) -> None:
        """Update mutable fields on a job and touch the parent session."""
        current = self.get_job(job_id)
        if current is None:
            raise KeyError(f"Unknown job_id: {job_id}")
        timestamp = utcnow().isoformat()
        with self.db.connection() as connection:
            connection.execute(
                """
                UPDATE jobs
                SET status = ?, stage = ?, attempt = ?, failure_code = ?,
                    degraded_mode = ?, message = ?, updated_at = ?
                WHERE job_id = ?
                """,
                (
                    (status or current.status).value,
                    (stage or current.stage).value,
                    attempt if attempt is not None else current.attempt,
                    failure_code,
                    int(
                        degraded_mode
                        if degraded_mode is not None
                        else current.degraded_mode
                    ),
                    message if message is not None else current.message,
                    timestamp,
                    job_id,
                ),
            )
            connection.execute(
                """
                UPDATE sessions
                SET updated_at = ?, latest_job_id = ?, degraded_mode = COALESCE(?, degraded_mode)
                WHERE session_id = ?
                """,
                (
                    timestamp,
                    job_id,
                    int(degraded_mode) if degraded_mode is not None else None,
                    current.session_id,
                ),
            )

    def set_session_state(
        self,
        session_id: str,
        *,
        status: SessionStatus,
        failure_code: str | None = None,
        degraded_mode: bool | None = None,
        report_path: str | None = None,
        executive_summary: str | None = None,
    ) -> None:
        """Update mutable fields on a session."""
        session = self.get_session(session_id)
        if session is None:
            raise KeyError(f"Unknown session_id: {session_id}")
        self.db.execute(
            """
            UPDATE sessions
            SET status = ?, failure_code = ?, degraded_mode = ?, report_path = ?, executive_summary = ?, updated_at = ?
            WHERE session_id = ?
            """,
            (
                status.value,
                failure_code,
                int(
                    degraded_mode
                    if degraded_mode is not None
                    else session.degraded_mode
                ),
                report_path if report_path is not None else session.report_path,
                executive_summary
                if executive_summary is not None
                else session.executive_summary,
                utcnow().isoformat(),
                session_id,
            ),
        )

    def save_preprocessing_summary(
        self, session_id: str, summary: PreprocessingSummary
    ) -> None:
        """Upsert the preprocessing summary for a session."""
        self.db.execute(
            """
            INSERT INTO preprocessing_summaries (session_id, payload_json)
            VALUES (?, ?)
            ON CONFLICT(session_id) DO UPDATE SET payload_json = excluded.payload_json
            """,
            (session_id, summary.model_dump_json()),
        )

    def save_runtime_metadata(
        self, session_id: str, metadata: SessionRuntimeMetadata
    ) -> None:
        """Upsert runtime metadata collected for a completed session."""
        self.db.execute(
            """
            INSERT INTO session_runtime_metadata (session_id, payload_json)
            VALUES (?, ?)
            ON CONFLICT(session_id) DO UPDATE SET payload_json = excluded.payload_json
            """,
            (session_id, metadata.model_dump_json()),
        )

    def replace_reviews(self, session_id: str, reviews: list[ReviewNormalized]) -> None:
        """Replace the persisted sanitized reviews for a session."""
        with self.db.connection() as connection:
            connection.execute(
                "DELETE FROM reviews WHERE session_id = ?", (session_id,)
            )
            connection.executemany(
                """
                INSERT INTO reviews (
                    session_id, review_id, source, created_at, rating, language,
                    app_version, text_normalized, text_anonymized, dedupe_hash,
                    flags_json, metadata_json, sentiment_score, cluster_id
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, NULL)
                """,
                [
                    (
                        review.session_id,
                        review.review_id,
                        review.source,
                        review.created_at.isoformat(),
                        review.rating,
                        review.language,
                        review.app_version,
                        review.text_normalized,
                        review.text_anonymized,
                        review.dedupe_hash,
                        _json_dumps(review.flags),
                        _json_dumps(review.metadata),
                    )
                    for review in reviews
                ],
            )

    def update_review_analysis(
        self,
        session_id: str,
        sentiment_by_review: dict[str, float],
        cluster_by_review: dict[str, str],
    ) -> None:
        """Persist sentiment scores and cluster assignments for reviews."""
        with self.db.connection() as connection:
            for review_id, sentiment in sentiment_by_review.items():
                connection.execute(
                    """
                    UPDATE reviews
                    SET sentiment_score = ?, cluster_id = ?
                    WHERE session_id = ? AND review_id = ?
                    """,
                    (
                        sentiment,
                        cluster_by_review.get(review_id),
                        session_id,
                        review_id,
                    ),
                )

    def replace_clusters(self, session_id: str, clusters: list[ClusterRecord]) -> None:
        """Replace the persisted cluster set for a session."""
        with self.db.connection() as connection:
            connection.execute(
                "DELETE FROM clusters WHERE session_id = ?", (session_id,)
            )
            connection.executemany(
                """
                INSERT INTO clusters (
                    session_id, cluster_id, label, summary, review_ids_json, top_quote_ids_json,
                    priority_score, sentiment_score, trend_delta, confidence, degraded_reason,
                    keywords_json, sources_json, size, anomaly_flag
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        session_id,
                        cluster.cluster_id,
                        cluster.label,
                        cluster.summary,
                        _json_dumps(cluster.review_ids),
                        _json_dumps(cluster.top_quote_ids),
                        cluster.priority_score,
                        cluster.sentiment_score,
                        cluster.trend_delta,
                        cluster.confidence,
                        cluster.degraded_reason,
                        _json_dumps(cluster.keywords),
                        _json_dumps(cluster.sources),
                        cluster.size,
                        int(cluster.anomaly_flag),
                    )
                    for cluster in clusters
                ],
            )

    def replace_alerts(self, session_id: str, alerts: list[AlertRecord]) -> None:
        """Replace the persisted alert set for a session."""
        with self.db.connection() as connection:
            connection.execute("DELETE FROM alerts WHERE session_id = ?", (session_id,))
            connection.executemany(
                """
                INSERT INTO alerts (
                    session_id, alert_id, cluster_id, type, severity, reason,
                    spike_ratio, insufficient_history, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        session_id,
                        alert.alert_id,
                        alert.cluster_id,
                        alert.type,
                        alert.severity,
                        alert.reason,
                        alert.spike_ratio,
                        int(alert.insufficient_history),
                        alert.created_at.isoformat(),
                    )
                    for alert in alerts
                ],
            )

    def add_chat_turn(self, session_id: str, role: str, content: str) -> None:
        """Append one chat message to the session transcript."""
        self.db.execute(
            """
            INSERT INTO chat_turns (session_id, role, content, created_at)
            VALUES (?, ?, ?, ?)
            """,
            (session_id, role, content, utcnow().isoformat()),
        )

    def get_recent_chat_turns(
        self, session_id: str, limit: int = 6
    ) -> list[dict[str, str]]:
        """Return recent chat turns ordered from oldest to newest."""
        rows = self.db.fetchall(
            """
            SELECT role, content
            FROM chat_turns
            WHERE session_id = ?
            ORDER BY id DESC
            LIMIT ?
            """,
            (session_id, limit),
        )
        return [
            {"role": row["role"], "content": row["content"]} for row in reversed(rows)
        ]

    def get_session(self, session_id: str) -> SessionRecord | None:
        """Fetch one session record by id."""
        row = self.db.fetchone(
            "SELECT * FROM sessions WHERE session_id = ?", (session_id,)
        )
        if row is None:
            return None
        return SessionRecord(
            session_id=row["session_id"],
            status=SessionStatus(row["status"]),
            latest_job_id=row["latest_job_id"],
            created_at=_parse_dt(row["created_at"]),
            updated_at=_parse_dt(row["updated_at"]),
            degraded_mode=bool(row["degraded_mode"]),
            failure_code=row["failure_code"],
            config_snapshot=_json_loads(row["config_snapshot_json"], {}),
            report_path=row["report_path"],
            executive_summary=row["executive_summary"],
        )

    def get_job(self, job_id: str) -> JobRecord | None:
        """Fetch one job record by id."""
        row = self.db.fetchone("SELECT * FROM jobs WHERE job_id = ?", (job_id,))
        if row is None:
            return None
        return JobRecord(
            job_id=row["job_id"],
            session_id=row["session_id"],
            status=JobStatus(row["status"]),
            stage=JobStage(row["stage"]),
            attempt=row["attempt"],
            failure_code=row["failure_code"],
            degraded_mode=bool(row["degraded_mode"]),
            message=row["message"],
            created_at=_parse_dt(row["created_at"]),
            updated_at=_parse_dt(row["updated_at"]),
        )

    def get_job_by_session(self, session_id: str) -> JobRecord | None:
        """Fetch the latest job associated with a session."""
        row = self.db.fetchone(
            """
            SELECT j.*
            FROM jobs j
            JOIN sessions s ON s.latest_job_id = j.job_id
            WHERE s.session_id = ?
            """,
            (session_id,),
        )
        if row is None:
            return None
        return self.get_job(str(row["job_id"]))

    def get_preprocessing_summary(self, session_id: str) -> PreprocessingSummary | None:
        """Fetch the preprocessing summary for a session."""
        row = self.db.fetchone(
            "SELECT payload_json FROM preprocessing_summaries WHERE session_id = ?",
            (session_id,),
        )
        if row is None:
            return None
        return PreprocessingSummary.model_validate_json(row["payload_json"])

    def get_reviews(self, session_id: str) -> list[ReviewNormalized]:
        """Fetch all persisted sanitized reviews for a session."""
        rows = self.db.fetchall(
            """
            SELECT *
            FROM reviews
            WHERE session_id = ?
            ORDER BY created_at ASC, review_id ASC
            """,
            (session_id,),
        )
        return [
            ReviewNormalized(
                review_id=row["review_id"],
                session_id=row["session_id"],
                source=row["source"],
                created_at=_parse_dt(row["created_at"]),
                rating=row["rating"],
                language=row["language"],
                app_version=row["app_version"],
                text_normalized=row["text_normalized"],
                text_anonymized=row["text_anonymized"],
                dedupe_hash=row["dedupe_hash"],
                flags=_json_loads(row["flags_json"], []),
                metadata=_json_loads(row["metadata_json"], {}),
            )
            for row in rows
        ]

    def get_runtime_metadata(self, session_id: str) -> SessionRuntimeMetadata | None:
        """Fetch persisted runtime metadata for a session, if present."""
        row = self.db.fetchone(
            "SELECT payload_json FROM session_runtime_metadata WHERE session_id = ?",
            (session_id,),
        )
        if row is None:
            return None
        payload = _json_loads(row["payload_json"], {})
        payload.setdefault("trace_correlation_id", "n/a")
        payload.setdefault("presentation_mode", "clustered")
        payload.setdefault("low_data_mode", False)
        payload.setdefault("trace_exporters_effective", [])
        payload.setdefault("trace_local_path", None)
        payload.setdefault("pii_backend_requested", "regex")
        payload.setdefault("pii_backend_effective", "regex")
        payload.setdefault("sentiment_backend_requested", "lexical")
        payload.setdefault("sentiment_backend_effective", "lexical")
        payload.setdefault("sentiment_model_effective", None)
        payload.setdefault("llm_call_count", 0)
        payload.setdefault("embedding_call_count", 0)
        payload.setdefault("prompt_tokens_total", 0)
        payload.setdefault("completion_tokens_total", 0)
        payload.setdefault("embedding_input_tokens_total", 0)
        payload.setdefault("estimated_cost_usd", 0.0)
        payload.setdefault("provider_usage_summary", {})
        payload.setdefault("weak_signal_cluster_ids", [])
        payload.setdefault("weak_signal_count", 0)
        payload.setdefault("mixed_sentiment_cluster_ids", [])
        payload.setdefault("mixed_sentiment_cluster_count", 0)
        payload.setdefault("mixed_language_review_count", 0)
        payload.setdefault("chroma_mode_effective", None)
        payload.setdefault("chroma_endpoint_effective", None)
        return SessionRuntimeMetadata.model_validate(payload)

    def get_quotes_for_cluster(
        self, session_id: str, cluster_id: str, limit: int = 3
    ) -> list[QuoteRecord]:
        """Fetch representative anonymized quotes for a cluster."""
        rows = self.db.fetchall(
            """
            SELECT review_id, cluster_id, text_anonymized, source, created_at
            FROM reviews
            WHERE session_id = ? AND cluster_id = ?
            ORDER BY ABS(sentiment_score) DESC, created_at DESC
            LIMIT ?
            """,
            (session_id, cluster_id, limit),
        )
        return [
            QuoteRecord(
                review_id=row["review_id"],
                cluster_id=row["cluster_id"],
                text=row["text_anonymized"],
                source=row["source"],
                created_at=_parse_dt(row["created_at"]),
            )
            for row in rows
        ]

    def get_clusters(self, session_id: str) -> list[ClusterRecord]:
        """Fetch all persisted clusters for a session."""
        rows = self.db.fetchall(
            """
            SELECT *
            FROM clusters
            WHERE session_id = ?
            ORDER BY priority_score DESC, size DESC, cluster_id ASC
            """,
            (session_id,),
        )
        return [
            ClusterRecord(
                cluster_id=row["cluster_id"],
                label=row["label"],
                summary=row["summary"],
                review_ids=_json_loads(row["review_ids_json"], []),
                top_quote_ids=_json_loads(row["top_quote_ids_json"], []),
                priority_score=float(row["priority_score"]),
                sentiment_score=float(row["sentiment_score"]),
                trend_delta=float(row["trend_delta"]),
                confidence=row["confidence"],
                degraded_reason=row["degraded_reason"],
                keywords=_json_loads(row["keywords_json"], []),
                sources=_json_loads(row["sources_json"], []),
                size=int(row["size"]),
                anomaly_flag=bool(row["anomaly_flag"]),
            )
            for row in rows
        ]

    def get_cluster(self, session_id: str, cluster_id: str) -> ClusterRecord | None:
        """Fetch one cluster by session and cluster id."""
        rows = [
            cluster
            for cluster in self.get_clusters(session_id)
            if cluster.cluster_id == cluster_id
        ]
        return rows[0] if rows else None

    def get_alerts(self, session_id: str) -> list[AlertRecord]:
        """Fetch all persisted alerts for a session."""
        rows = self.db.fetchall(
            """
            SELECT *
            FROM alerts
            WHERE session_id = ?
            ORDER BY created_at DESC, severity DESC
            """,
            (session_id,),
        )
        return [
            AlertRecord(
                alert_id=row["alert_id"],
                cluster_id=row["cluster_id"],
                type=row["type"],
                severity=row["severity"],
                reason=row["reason"],
                spike_ratio=row["spike_ratio"],
                insufficient_history=bool(row["insufficient_history"]),
                created_at=_parse_dt(row["created_at"]),
            )
            for row in rows
        ]

    def get_report(self, session_id: str) -> ReportArtifact | None:
        """Load the persisted report artifact for a session, if present."""
        session = self.get_session(session_id)
        if session is None or session.report_path is None:
            return None
        try:
            markdown = open(session.report_path, "r", encoding="utf-8").read()
        except FileNotFoundError:
            return None
        return ReportArtifact(
            report_id=f"report_{session_id}",
            session_id=session_id,
            path=session.report_path,
            executive_summary=session.executive_summary or "",
            markdown=markdown,
            generated_at=session.updated_at,
            degraded_mode=session.degraded_mode,
        )

    def session_exists(self, session_id: str) -> bool:
        """Return whether a session exists."""
        row = self.db.fetchone(
            "SELECT 1 AS ok FROM sessions WHERE session_id = ?", (session_id,)
        )
        return row is not None

    def get_session_detail(self, session_id: str) -> SessionDetail:
        """Build the compound session view used by the API layer."""
        session = self.get_session(session_id)
        if session is None:
            raise KeyError(f"Unknown session_id: {session_id}")
        job = self.get_job_by_session(session_id)
        if job is None:
            raise KeyError(f"No job linked to session_id: {session_id}")
        runtime_metadata = self.get_runtime_metadata(session_id)
        clusters = self.get_clusters(session_id)
        weak_signal_ids = set(
            runtime_metadata.weak_signal_cluster_ids if runtime_metadata else []
        )
        top_clusters = [
            cluster for cluster in clusters if cluster.cluster_id not in weak_signal_ids
        ]
        weak_signals = [
            cluster for cluster in clusters if cluster.cluster_id in weak_signal_ids
        ]
        reviews = self.get_reviews(session_id)
        simple_list_reviews = [
            ReviewPreview(
                review_id=review.review_id,
                source=review.source,
                created_at=review.created_at,
                language=review.language,
                text=review.text_anonymized,
                flags=review.flags,
                cluster_id=next(
                    (
                        cluster.cluster_id
                        for cluster in clusters
                        if review.review_id in cluster.review_ids
                    ),
                    None,
                ),
            )
            for review in reviews
        ]
        warnings: list[str] = []
        if runtime_metadata and runtime_metadata.low_data_mode:
            warnings.append(
                "Low-data mode is active because fewer than 30 reviews were uploaded."
            )
        if weak_signals:
            warnings.append(
                f"{len(weak_signals)} weak-signal clusters were excluded from the top themes."
            )
        return SessionDetail(
            session=session,
            job=job,
            preprocessing_summary=self.get_preprocessing_summary(session_id),
            clusters=clusters,
            top_clusters=top_clusters,
            weak_signals=weak_signals,
            simple_list_reviews=simple_list_reviews,
            presentation_mode=(
                runtime_metadata.presentation_mode
                if runtime_metadata is not None
                else "clustered"
            ),
            warnings=warnings,
            alerts=self.get_alerts(session_id),
            report=self.get_report(session_id),
            runtime_metadata=runtime_metadata,
        )

    def list_recovery_jobs(self) -> list[JobRecord]:
        """List jobs that should be re-queued after a restart."""
        rows = self.db.fetchall(
            """
            SELECT job_id
            FROM jobs
            WHERE status IN (?, ?, ?)
            ORDER BY updated_at ASC
            """,
            (
                JobStatus.running.value,
                JobStatus.retrying.value,
                JobStatus.degraded_running.value,
            ),
        )
        return [
            self.get_job(str(row["job_id"]))
            for row in rows
            if self.get_job(str(row["job_id"])) is not None
        ]

    def get_job_events(self, session_id: str) -> list[dict[str, str]]:
        """Return ordered stage events for a session."""
        rows = self.db.fetchall(
            """
            SELECT stage, event, level, message, correlation_id, metadata_json, created_at
            FROM job_events
            WHERE session_id = ?
            ORDER BY id ASC
            """,
            (session_id,),
        )
        return [
            {
                "stage": row["stage"],
                "event": row["event"],
                "level": row["level"],
                "message": row["message"],
                "correlation_id": row["correlation_id"],
                "metadata": _json_loads(row["metadata_json"], {}),
                "created_at": row["created_at"],
            }
            for row in rows
        ]

    def update_worker_heartbeat(self, payload: dict[str, Any]) -> None:
        """Persist the latest worker heartbeat payload."""
        self.db.upsert_system_state("worker_heartbeat", payload)

    def get_worker_heartbeat(self) -> sqlite3.Row | None:
        """Fetch the latest worker heartbeat row."""
        return self.db.get_system_state("worker_heartbeat")
