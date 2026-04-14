from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from time import perf_counter
from typing import Any

from pfia.analysis import (
    AnalysisArtifacts,
    analyze_reviews,
    build_alerts,
    partition_clusters_for_display,
)
from pfia.config import Settings, get_settings
from pfia.db import Database, utcnow
from pfia.errors import PFIAError
from pfia.llm_agents import (
    explain_alerts_with_llm,
    generate_executive_summary_with_llm,
    review_clusters_with_llm,
    review_preprocessing_flags_with_llm,
    refine_clusters_with_llm,
)
from pfia.metrics import Metrics
from pfia.models import (
    JobStage,
    JobStatus,
    SessionRuntimeMetadata,
    SessionStatus,
    UploadResponse,
)
from pfia.observability import (
    SessionRunObserver,
    bind_observer,
    get_current_observer,
    record_span,
)
from pfia.orchestrator import JobLangGraphOrchestrator, langgraph_available
from pfia.preprocessing import (
    preprocess_upload,
    refresh_summary_flag_counts,
    summarize_preprocessing_backends,
    write_sanitized_jsonl,
)
from pfia.qna import answer_question
from pfia.reporting import build_report_markdown, write_report
from pfia.repository import Repository
from pfia.retrieval import RetrievalBuildResult, build_retrieval_index
from pfia.tracing import CompositeTraceSink, build_trace_sink
from pfia.utils import generate_id


FAILED_STATUS_BY_CODE = {
    "INPUT_SCHEMA_INVALID": JobStatus.failed_input,
    "INPUT_LIMIT_EXCEEDED": JobStatus.failed_input,
    "FAILED_INPUT": JobStatus.failed_input,
    "PRIVACY_GATE_FAILED": JobStatus.failed_privacy,
    "FAILED_PRIVACY": JobStatus.failed_privacy,
    "FAILED_PERSISTENCE": JobStatus.failed_persistence,
}


@dataclass
class AppContext:
    """Runtime container for shared application dependencies."""

    settings: Settings
    db: Database
    repo: Repository
    metrics: Metrics
    trace_sink: CompositeTraceSink


def build_app_context(settings: Settings | None = None) -> AppContext:
    """Construct the shared dependency container for PFIA.

    Args:
        settings: Optional explicit settings instance.

    Returns:
        Initialized application context with storage and metrics dependencies.
    """
    resolved_settings = settings or get_settings()
    resolved_settings.ensure_directories()
    db = Database(resolved_settings.db_path)
    return AppContext(
        settings=resolved_settings,
        db=db,
        repo=Repository(db),
        metrics=Metrics(),
        trace_sink=build_trace_sink(resolved_settings),
    )


class PFIAService:
    """High-level orchestration facade for uploads, jobs, reports, and Q&A."""

    def __init__(self, context: AppContext):
        """Store shared runtime dependencies for service operations.

        Args:
            context: Initialized dependency container.
        """
        self.context = context
        self.settings = context.settings
        self.repo = context.repo
        self.metrics = context.metrics
        self._job_orchestrator: JobLangGraphOrchestrator | None = None

    def upload_file(
        self, filename: str, content: bytes, content_type: str | None = None
    ) -> UploadResponse:
        """Validate an upload, persist it, and enqueue a new job.

        Args:
            filename: Original uploaded filename.
            content: Raw file bytes.
            content_type: Optional MIME type from the client.

        Returns:
            Upload response containing the new session and job identifiers.

        Raises:
            PFIAError: If the filename is missing, the file is too large, or the queue is full.
        """
        if not filename:
            raise PFIAError(
                "INPUT_SCHEMA_INVALID", "Uploaded file must have a filename."
            )
        if len(content) > self.settings.max_upload_size_bytes:
            raise PFIAError(
                "INPUT_LIMIT_EXCEEDED",
                f"Upload is too large. Max size is {self.settings.max_upload_size_bytes // (1024 * 1024)} MB.",
            )
        queue_depth = self.repo.get_queue_depth()
        if queue_depth >= self.settings.max_queue_depth + 1:
            raise PFIAError(
                "QUEUE_FULL",
                "Queue is full. Please wait for the current jobs to finish.",
                status_code=429,
            )

        session_id = generate_id("sess")
        job_id = generate_id("job")
        target_path = self.settings.raw_dir / f"{session_id}_{filename}"
        target_path.write_bytes(content)
        config_snapshot = {
            "upload_path": str(target_path),
            "filename": filename,
            "content_type": content_type or "application/octet-stream",
            "max_batch_size": self.settings.max_batch_size,
            "orchestrator_backend": self.settings.orchestrator_backend,
            "retrieval_backend": self.settings.retrieval_backend,
            "embedding_backend": self.settings.embedding_backend,
            "embedding_primary_model": self.settings.embedding_primary_model,
            "embedding_fallback_model": self.settings.embedding_fallback_model,
            "generation_backend": self.settings.generation_backend,
            "llm_primary_model": self.settings.llm_primary_model,
            "llm_fallback_model": self.settings.llm_fallback_model,
        }
        self.repo.create_session_and_job(session_id, job_id, config_snapshot)
        self.metrics.queue_depth.set(self.repo.get_queue_depth())
        return UploadResponse(session_id=session_id, job_id=job_id, status="QUEUED")

    def process_next_job(self) -> str | None:
        """Process the oldest queued job, if one exists.

        Returns:
            Processed job id or ``None`` when the queue is empty.
        """
        job_id = self.repo.get_next_queued_job_id()
        if job_id is None:
            self.metrics.queue_depth.set(self.repo.get_queue_depth())
            return None
        self.process_job(job_id)
        self.metrics.queue_depth.set(self.repo.get_queue_depth())
        return job_id

    def process_job(self, job_id: str) -> None:
        """Execute the full batch-processing pipeline for one job.

        Args:
            job_id: Identifier of the queued job to process.

        Raises:
            KeyError: If the job or its session cannot be found.
            PFIAError: If a pipeline stage fails with a structured application error.
        """
        job = self.repo.get_job(job_id)
        if job is None:
            raise KeyError(f"Unknown job_id: {job_id}")
        session = self.repo.get_session(job.session_id)
        if session is None:
            raise KeyError(f"Unknown session for job_id: {job_id}")

        started = perf_counter()
        degraded_mode = False
        orchestrator_backend_effective = self._effective_orchestrator_backend()
        correlation_id = generate_id("corr")
        observer = SessionRunObserver(
            repo=self.repo,
            metrics=self.metrics,
            trace_sink=self.context.trace_sink,
            session_id=session.session_id,
            job_id=job_id,
            correlation_id=correlation_id,
        )

        with bind_observer(observer):
            try:
                if orchestrator_backend_effective == "langgraph":
                    final_state = self._get_job_orchestrator().run(job_id, session)
                else:
                    final_state = self._process_job_linear(job_id, session)
                degraded_mode = bool(final_state.get("degraded_mode"))
                final_job_status = (
                    JobStatus.degraded_completed
                    if degraded_mode
                    else JobStatus.completed
                )
                self.metrics.job_total.labels(status=final_job_status.value).inc()
                if degraded_mode:
                    self.metrics.degraded_jobs_total.inc()
            except PFIAError as exc:
                failed_status = FAILED_STATUS_BY_CODE.get(
                    exc.code, JobStatus.failed_runtime
                )
                current_job = self.repo.get_job(job_id)
                current_stage = (
                    current_job.stage if current_job is not None else job.stage
                )
                self.repo.set_job_state(
                    job_id,
                    status=failed_status,
                    stage=current_stage,
                    failure_code=exc.code,
                    degraded_mode=degraded_mode,
                    message=exc.message,
                )
                self.repo.set_session_state(
                    session.session_id,
                    status=SessionStatus.failed,
                    failure_code=exc.code,
                    degraded_mode=degraded_mode,
                )
                self.repo.log_event(
                    job_id,
                    session.session_id,
                    current_stage,
                    "job.failed",
                    "ERROR",
                    f"{exc.code}: {exc.message}",
                    correlation_id=correlation_id,
                    metadata={"error_code": exc.code},
                )
                self.metrics.job_total.labels(status=failed_status.value).inc()
                raise
            finally:
                elapsed = perf_counter() - started
                final = self.repo.get_job(job_id)
                if final is not None:
                    self.metrics.job_latency_seconds.labels(
                        status=final.status.value
                    ).observe(elapsed)

    def _effective_orchestrator_backend(self) -> str:
        """Resolve the effective batch orchestrator backend for this runtime."""

        if self.settings.orchestrator_backend == "langgraph" and langgraph_available():
            return "langgraph"
        return "linear"

    def _get_job_orchestrator(self) -> JobLangGraphOrchestrator:
        """Lazily build the shared LangGraph job orchestrator."""

        if self._job_orchestrator is None:
            self._job_orchestrator = JobLangGraphOrchestrator(self)
        return self._job_orchestrator

    def _process_job_linear(self, job_id: str, session) -> dict[str, Any]:
        """Execute the legacy linear batch pipeline as a safe fallback."""

        self.repo.set_job_state(
            job_id,
            status=JobStatus.running,
            stage=JobStage.validate_input,
            message="Validating upload",
        )
        self.repo.set_session_state(session.session_id, status=SessionStatus.processing)
        self.repo.log_event(
            job_id,
            session.session_id,
            JobStage.validate_input,
            "job.validate.start",
            "INFO",
            "Validating upload.",
        )
        upload_path = Path(session.config_snapshot["upload_path"])
        if not upload_path.exists():
            raise PFIAError("FAILED_INPUT", "Uploaded file is missing from storage.")

        (
            reviews,
            summary,
            preprocess_agent_meta,
            preprocessing_runtime_meta,
        ) = self._run_preprocess(job_id, session.session_id, upload_path)
        analysis = self._run_analysis(job_id, session.session_id, reviews, summary)
        degraded_mode = bool(analysis.degraded_mode)
        report_path = self._run_reporting(
            job_id,
            session,
            summary,
            reviews,
            analysis,
            degraded_mode,
            preprocess_agent_meta,
            preprocessing_runtime_meta,
            orchestrator_backend_effective="linear",
        )
        self._finalize_success(
            job_id,
            session.session_id,
            degraded_mode=degraded_mode,
            report_path=report_path,
        )
        return {
            "degraded_mode": degraded_mode,
            "report_path": report_path,
        }

    def _finalize_success(
        self,
        job_id: str,
        session_id: str,
        *,
        degraded_mode: bool,
        report_path: Path,
    ) -> None:
        """Persist final success state for a completed batch job."""

        final_job_status = (
            JobStatus.degraded_completed if degraded_mode else JobStatus.completed
        )
        final_session_status = (
            SessionStatus.degraded_completed
            if degraded_mode
            else SessionStatus.completed
        )
        self.repo.set_job_state(
            job_id,
            status=final_job_status,
            stage=JobStage.finalize,
            degraded_mode=degraded_mode,
            message="Job completed successfully",
        )
        self.repo.set_session_state(
            session_id,
            status=final_session_status,
            degraded_mode=degraded_mode,
            report_path=str(report_path),
        )
        self.repo.log_event(
            job_id,
            session_id,
            JobStage.finalize,
            "job.finalize",
            "INFO",
            "Job finalized.",
        )

    def _run_preprocess(self, job_id: str, session_id: str, upload_path: Path):
        """Run preprocessing and persist sanitized review artifacts.

        Args:
            job_id: Owning job identifier.
            session_id: Owning session identifier.
            upload_path: Path to the raw uploaded file.

        Returns:
            Tuple of sanitized reviews, preprocessing summary, runtime agent metadata,
            and backend diagnostics.
        """
        self.repo.set_job_state(
            job_id, stage=JobStage.preprocess, message="Preprocessing reviews"
        )
        self.repo.log_event(
            job_id,
            session_id,
            JobStage.preprocess,
            "job.preprocess.start",
            "INFO",
            "Preprocessing started.",
        )
        reviews, summary = preprocess_upload(upload_path, session_id, self.settings)
        reviewed_reviews, preprocess_agent_meta = review_preprocessing_flags_with_llm(
            reviews,
            self.settings,
        )
        reviews = reviewed_reviews
        summary = refresh_summary_flag_counts(summary, reviews)
        preprocessing_runtime_meta = summarize_preprocessing_backends(reviews)
        sanitized_path = self.settings.sanitized_dir / f"{session_id}.jsonl"
        write_sanitized_jsonl(sanitized_path, reviews)
        self.repo.save_preprocessing_summary(session_id, summary)
        self.repo.replace_reviews(session_id, reviews)
        if preprocess_agent_meta.get("used"):
            self.repo.log_event(
                job_id,
                session_id,
                JobStage.preprocess,
                "job.preprocess.llm_review",
                "INFO",
                "LLM borderline review adjusted preprocessing flags.",
            )
        if summary.quarantined_records:
            self.metrics.pii_quarantine_total.inc(summary.quarantined_records)
        if summary.injection_hits:
            self.metrics.injection_detected_total.inc(summary.injection_hits)
        self.repo.log_event(
            job_id,
            session_id,
            JobStage.preprocess,
            "job.preprocess.done",
            "INFO",
            f"Kept {summary.kept_records} of {summary.total_records} reviews after preprocessing.",
        )
        return reviews, summary, preprocess_agent_meta, preprocessing_runtime_meta

    def _run_analysis(
        self, job_id: str, session_id: str, reviews, summary
    ) -> AnalysisArtifacts:
        """Run clustering, sentiment scoring, and anomaly detection.

        Args:
            job_id: Owning job identifier.
            session_id: Owning session identifier.
            reviews: Sanitized reviews ready for analysis.
            summary: Preprocessing summary, reserved for future analysis hooks.

        Returns:
            Analysis artifact bundle.
        """
        _ = summary
        self.repo.set_job_state(
            job_id, stage=JobStage.embed, message="Building embeddings"
        )
        self.repo.log_event(
            job_id,
            session_id,
            JobStage.embed,
            "job.embed.start",
            "INFO",
            "Embedding pipeline started.",
        )
        analysis = analyze_reviews(session_id, reviews, self.settings)
        self.repo.set_job_state(
            job_id,
            status=JobStatus.degraded_running
            if analysis.degraded_mode
            else JobStatus.running,
            stage=JobStage.cluster,
            degraded_mode=analysis.degraded_mode,
            message="Clustering and scoring feedback",
        )
        self.repo.log_event(
            job_id,
            session_id,
            JobStage.cluster,
            "job.cluster.done",
            "INFO",
            f"Built {len(analysis.clusters)} clusters. Quality={analysis.diagnostics['quality_score']:.3f}.",
        )
        if analysis.diagnostics.get("low_data_mode"):
            self.repo.log_event(
                job_id,
                session_id,
                JobStage.cluster,
                "job.cluster.low_data_mode",
                "WARN",
                "Low-data mode activated; switching presentation to simple-list-first.",
                metadata={
                    "low_data_review_threshold": self.settings.low_data_review_threshold,
                    "records_kept": len(reviews),
                },
            )
        reviewed_clusters, updated_mapping, cluster_review_meta = (
            review_clusters_with_llm(
                analysis.clusters,
                reviews,
                analysis.cluster_by_review,
                self.settings,
            )
        )
        analysis.clusters = reviewed_clusters
        analysis.cluster_by_review = updated_mapping
        analysis.alerts = build_alerts(analysis.clusters, reviews)
        analysis.diagnostics["cluster_review_agent"] = cluster_review_meta
        self.repo.log_event(
            job_id,
            session_id,
            JobStage.cluster,
            "job.cluster.review",
            "INFO",
            "Cluster review agent finished merge/split evaluation.",
        )
        self.repo.set_job_state(
            job_id,
            status=JobStatus.degraded_running
            if analysis.degraded_mode
            else JobStatus.running,
            stage=JobStage.label_and_summarize,
            degraded_mode=analysis.degraded_mode,
            message="Refining labels and summaries",
        )
        self.repo.log_event(
            job_id,
            session_id,
            JobStage.label_and_summarize,
            "job.label.start",
            "INFO",
            "Label and summary refinement started.",
        )
        refined_clusters, agent_meta = refine_clusters_with_llm(
            analysis.clusters,
            reviews,
            self.settings,
        )
        analysis.clusters = refined_clusters
        analysis.diagnostics["taxonomy_agent"] = agent_meta
        self.repo.log_event(
            job_id,
            session_id,
            JobStage.label_and_summarize,
            "job.label.done",
            "INFO",
            "Label and summary refinement completed.",
        )
        explained_alerts, anomaly_meta = explain_alerts_with_llm(
            analysis.alerts,
            analysis.clusters,
            self.settings,
        )
        analysis.alerts = explained_alerts
        analysis.diagnostics["anomaly_explainer_agent"] = anomaly_meta
        self.repo.set_job_state(
            job_id,
            status=JobStatus.degraded_running
            if analysis.degraded_mode
            else JobStatus.running,
            stage=JobStage.score,
            degraded_mode=analysis.degraded_mode,
            message="Persisting scores and ranked clusters",
        )
        self.repo.log_event(
            job_id,
            session_id,
            JobStage.score,
            "job.score.done",
            "INFO",
            "Priority scoring completed.",
        )
        self.repo.update_review_analysis(
            session_id, analysis.sentiment_by_review, analysis.cluster_by_review
        )
        self.repo.replace_clusters(session_id, analysis.clusters)
        self.repo.set_job_state(
            job_id,
            status=JobStatus.degraded_running
            if analysis.degraded_mode
            else JobStatus.running,
            stage=JobStage.detect_anomalies,
            degraded_mode=analysis.degraded_mode,
            message="Detecting anomalies",
        )
        self.repo.replace_alerts(session_id, analysis.alerts)
        self.repo.log_event(
            job_id,
            session_id,
            JobStage.detect_anomalies,
            "job.anomaly.done",
            "INFO",
            f"Generated {len(analysis.alerts)} alerts (including insufficient history notes).",
        )
        return analysis

    def _run_reporting(
        self,
        job_id: str,
        session,
        summary,
        reviews,
        analysis: AnalysisArtifacts,
        degraded_mode: bool,
        preprocess_agent_meta: dict[str, Any],
        preprocessing_runtime_meta: dict[str, Any],
        *,
        orchestrator_backend_effective: str,
    ) -> Path:
        """Build retrieval artifacts and the final Markdown report.

        Args:
            job_id: Owning job identifier.
            session: Owning session record with the original config snapshot.
            summary: Preprocessing summary used in report rendering.
            reviews: Sanitized reviews with downstream annotations.
            analysis: Analysis outputs used by reporting and retrieval.
            degraded_mode: Whether the run completed in degraded mode.
            preprocess_agent_meta: Runtime metadata for the preprocessing review agent.
            preprocessing_runtime_meta: Effective preprocessing backend diagnostics.
            orchestrator_backend_effective: Effective batch orchestrator backend for this run.

        Returns:
            Path to the persisted Markdown report.
        """
        session_id = session.session_id
        self.repo.set_job_state(
            job_id,
            status=JobStatus.degraded_running if degraded_mode else JobStatus.running,
            stage=JobStage.index_for_retrieval,
            degraded_mode=degraded_mode,
            message="Building retrieval index",
        )
        executive_summary_override, report_agent_meta = (
            generate_executive_summary_with_llm(
                session_id,
                summary,
                analysis.clusters[: self.settings.report_top_clusters],
                analysis.alerts,
                degraded_mode=degraded_mode,
                diagnostics=analysis.diagnostics,
                settings=self.settings,
            )
        )
        analysis.diagnostics["report_agent"] = report_agent_meta
        top_clusters, weak_signals = partition_clusters_for_display(
            analysis.clusters,
            list(analysis.diagnostics.get("weak_signal_cluster_ids", [])),
        )
        report_top_clusters = top_clusters[: self.settings.report_top_clusters]
        review_payload = []
        for review in reviews:
            cluster_id = analysis.cluster_by_review.get(review.review_id)
            if not cluster_id:
                continue
            review_payload.append(
                {
                    "review_id": review.review_id,
                    "cluster_id": cluster_id,
                    "text": review.text_anonymized,
                    "source": review.source,
                    "created_at": review.created_at.isoformat(),
                    "sentiment_score": analysis.sentiment_by_review.get(
                        review.review_id, 0.0
                    ),
                }
            )
        preview_markdown, preview_executive_summary = build_report_markdown(
            session_id,
            summary,
            analysis.clusters,
            analysis.alerts,
            degraded_mode=degraded_mode,
            diagnostics=analysis.diagnostics,
            executive_summary_override=executive_summary_override,
            runtime_metadata=None,
            top_clusters=report_top_clusters,
            weak_signals=weak_signals,
            reviews=reviews,
        )
        retrieval_build = build_retrieval_index(
            session_id,
            review_payload,
            analysis.clusters,
            report_sections={
                "executive_summary": preview_executive_summary,
                "top_themes": "\n".join(preview_markdown.splitlines()[0:40]),
                "alerts": "\n".join(
                    line
                    for line in preview_markdown.splitlines()
                    if "Alerts" in line or line.startswith("- ")
                ),
            },
            index_path=self.settings.indexes_dir / f"{session_id}.pkl",
            settings=self.settings,
            retrieval_backend=self.settings.retrieval_backend,
            chroma_path=self.settings.chroma_persist_dir,
        )
        runtime_metadata = self._build_runtime_metadata(
            session,
            summary,
            analysis,
            preprocess_agent_meta=preprocess_agent_meta,
            preprocessing_runtime_meta=preprocessing_runtime_meta,
            retrieval_build=retrieval_build,
            orchestrator_backend_effective=orchestrator_backend_effective,
            top_clusters=report_top_clusters,
            weak_signals=weak_signals,
        )
        report_markdown, executive_summary = build_report_markdown(
            session_id,
            summary,
            analysis.clusters,
            analysis.alerts,
            degraded_mode=degraded_mode,
            diagnostics=analysis.diagnostics,
            executive_summary_override=executive_summary_override,
            runtime_metadata=runtime_metadata,
            top_clusters=report_top_clusters,
            weak_signals=weak_signals,
            reviews=reviews,
        )
        self.repo.save_runtime_metadata(session_id, runtime_metadata)
        self.metrics.session_cost_usd.labels(session_id=session_id).set(
            runtime_metadata.estimated_cost_usd
        )
        if runtime_metadata.estimated_cost_usd > 0:
            self.metrics.cost_usd_total.inc(runtime_metadata.estimated_cost_usd)
        self.repo.log_event(
            job_id,
            session_id,
            JobStage.index_for_retrieval,
            "job.index.done",
            "INFO",
            f"Retrieval index saved ({retrieval_build.effective_backend}).",
            metadata={
                "retrieval_backend_effective": retrieval_build.effective_backend,
                "trace_correlation_id": runtime_metadata.trace_correlation_id,
            },
        )

        self.repo.set_job_state(
            job_id,
            status=JobStatus.degraded_running if degraded_mode else JobStatus.running,
            stage=JobStage.build_report,
            degraded_mode=degraded_mode,
            message="Writing Markdown report",
        )
        report_path = self.settings.reports_dir / f"{session_id}.md"
        artifact = write_report(
            report_path, report_markdown, session_id, executive_summary, degraded_mode
        )
        self.repo.set_session_state(
            session_id,
            status=SessionStatus.processing,
            degraded_mode=degraded_mode,
            report_path=artifact.path,
            executive_summary=artifact.executive_summary,
        )
        self.repo.log_event(
            job_id,
            session_id,
            JobStage.build_report,
            "job.report.done",
            "INFO",
            "Markdown report written.",
        )
        return report_path

    def _build_runtime_metadata(
        self,
        session,
        summary,
        analysis: AnalysisArtifacts,
        *,
        preprocess_agent_meta: dict[str, Any],
        preprocessing_runtime_meta: dict[str, Any],
        retrieval_build: RetrievalBuildResult,
        orchestrator_backend_effective: str,
        top_clusters,
        weak_signals,
    ) -> SessionRuntimeMetadata:
        """Build a compact runtime metadata snapshot for one completed session.

        Args:
            session: Owning session record with upload config.
            summary: Final preprocessing summary for the run.
            analysis: Analysis artifacts with diagnostics populated.
            preprocess_agent_meta: Runtime metadata for the preprocessing review agent.
            preprocessing_runtime_meta: Effective preprocessing backend diagnostics.
            retrieval_build: Effective retrieval backend information for the run.
            orchestrator_backend_effective: Effective batch orchestrator backend for the run.
            top_clusters: Presentation-filtered top theme clusters.
            weak_signals: Presentation-filtered weak-signal clusters.

        Returns:
            Structured runtime metadata used by the API and report.
        """
        agent_usage = {
            "preprocess_review_agent": preprocess_agent_meta,
            "cluster_review_agent": analysis.diagnostics.get(
                "cluster_review_agent", {}
            ),
            "taxonomy_agent": analysis.diagnostics.get("taxonomy_agent", {}),
            "anomaly_explainer_agent": analysis.diagnostics.get(
                "anomaly_explainer_agent", {}
            ),
            "report_agent": analysis.diagnostics.get("report_agent", {}),
        }
        llm_agent_used = any(
            isinstance(meta, dict)
            and meta.get("used") is True
            and meta.get("mode") in {"openai", "mistral", "anthropic"}
            for meta in agent_usage.values()
        )
        generation_modes = sorted(
            {
                str(meta.get("mode"))
                for meta in agent_usage.values()
                if isinstance(meta, dict)
                and meta.get("used") is True
                and str(meta.get("mode")) not in {"local", "fallback", "unknown"}
            }
        )
        if not generation_modes:
            generation_backend_effective = "local"
        elif len(generation_modes) == 1:
            generation_backend_effective = generation_modes[0]
        else:
            generation_backend_effective = "mixed"
        observer = get_current_observer()
        telemetry = (
            observer.snapshot() if isinstance(observer, SessionRunObserver) else None
        )
        return SessionRuntimeMetadata(
            runtime_profile="llm-enhanced" if llm_agent_used else "deterministic",
            presentation_mode=(
                "simple_list"
                if bool(analysis.diagnostics.get("low_data_mode"))
                else "clustered"
            ),
            low_data_mode=bool(analysis.diagnostics.get("low_data_mode")),
            trace_correlation_id=(
                telemetry.correlation_id if telemetry is not None else "n/a"
            ),
            trace_exporters_effective=list(self.context.trace_sink.effective_names),
            trace_local_path=str(self.settings.traces_dir / "events.jsonl"),
            orchestrator_backend_requested=self.settings.orchestrator_backend,
            orchestrator_backend_effective=orchestrator_backend_effective,
            generation_backend_requested=self.settings.generation_backend,
            generation_backend_effective=generation_backend_effective,
            retrieval_backend_requested=self.settings.retrieval_backend,
            retrieval_backend_effective=retrieval_build.effective_backend,
            pii_backend_requested=self.settings.pii_backend,
            pii_backend_effective=str(
                preprocessing_runtime_meta.get("pii_backend_effective", "regex")
            ),
            sentiment_backend_requested=self.settings.sentiment_backend,
            sentiment_backend_effective=str(
                analysis.diagnostics.get("sentiment_backend_effective", "lexical")
            ),
            sentiment_model_effective=analysis.diagnostics.get(
                "sentiment_model_effective"
            ),
            embedding_backend=retrieval_build.embedding_backend_effective,
            embedding_backend_requested=self.settings.embedding_backend,
            embedding_backend_effective=retrieval_build.embedding_backend_effective,
            embedding_model_effective=retrieval_build.embedding_model_effective
            or analysis.diagnostics.get("embedding_model_effective"),
            openai_generation_enabled=self.settings.openai_generation_enabled,
            mistral_fallback_enabled=self.settings.mistral_generation_enabled,
            anthropic_fallback_enabled=self.settings.anthropic_generation_enabled,
            llm_primary_model=(
                self.settings.llm_primary_model
                if self.settings.llm_generation_enabled
                else None
            ),
            llm_call_count=telemetry.llm_call_count if telemetry is not None else 0,
            embedding_call_count=(
                telemetry.embedding_call_count if telemetry is not None else 0
            ),
            prompt_tokens_total=(
                telemetry.prompt_tokens_total if telemetry is not None else 0
            ),
            completion_tokens_total=(
                telemetry.completion_tokens_total if telemetry is not None else 0
            ),
            embedding_input_tokens_total=(
                telemetry.embedding_input_tokens_total if telemetry is not None else 0
            ),
            estimated_cost_usd=round(
                telemetry.estimated_cost_usd if telemetry is not None else 0.0, 6
            ),
            provider_usage_summary=(
                telemetry.provider_usage_summary if telemetry is not None else {}
            ),
            input_filename=str(session.config_snapshot.get("filename") or ""),
            input_content_type=str(session.config_snapshot.get("content_type") or ""),
            records_total=summary.total_records,
            records_kept=summary.kept_records,
            top_cluster_ids=[cluster.cluster_id for cluster in top_clusters],
            weak_signal_cluster_ids=[cluster.cluster_id for cluster in weak_signals],
            weak_signal_count=len(weak_signals),
            mixed_sentiment_cluster_ids=list(
                analysis.diagnostics.get("mixed_sentiment_cluster_ids", [])
            ),
            mixed_sentiment_cluster_count=int(
                analysis.diagnostics.get("mixed_sentiment_cluster_count", 0)
            ),
            mixed_language_review_count=int(
                analysis.diagnostics.get("mixed_language_review_count", 0)
            ),
            data_dir=str(self.settings.data_dir),
            embedded_worker=bool(self.settings.embedded_worker),
            chroma_persist_dir=str(self.settings.chroma_persist_dir),
            chroma_mode_effective=retrieval_build.chroma_mode_effective,
            chroma_endpoint_effective=retrieval_build.chroma_endpoint_effective,
            agent_usage=agent_usage,
        )

    def get_session_detail(self, session_id: str) -> dict[str, Any]:
        """Return a session view augmented with ordered stage events.

        Args:
            session_id: Session identifier.

        Returns:
            JSON-serializable session payload.
        """
        detail = self.repo.get_session_detail(session_id)
        return {
            **detail.model_dump(mode="json"),
            "events": self.repo.get_job_events(session_id),
        }

    def chat(self, session_id: str, question: str) -> dict[str, Any]:
        """Answer a grounded question for a completed session.

        Args:
            session_id: Session identifier.
            question: User question.

        Returns:
            JSON-serializable grounded answer payload.

        Raises:
            PFIAError: If the session does not exist or evidence cannot be found.
        """
        session = self.repo.get_session(session_id)
        if session is None:
            raise PFIAError(
                "SESSION_NOT_FOUND", "Session was not found.", status_code=404
            )
        session_ready = session.status in {
            SessionStatus.completed,
            SessionStatus.degraded_completed,
        }
        started = perf_counter()
        correlation_id = generate_id("corr")
        observer = SessionRunObserver(
            repo=self.repo,
            metrics=self.metrics,
            trace_sink=self.context.trace_sink,
            session_id=session_id,
            job_id=session.latest_job_id,
            correlation_id=correlation_id,
        )
        with bind_observer(observer):
            record_span(
                stage="QNA",
                event="qna.retrieve",
                level="INFO",
                message="Grounded Q&A retrieval started.",
                metadata={"question": question[:200]},
            )
            answer = answer_question(
                self.settings.indexes_dir / f"{session_id}.pkl",
                session_ready,
                question,
                settings=self.settings,
                chat_history=self.repo.get_recent_chat_turns(session_id, limit=6),
            )
            record_span(
                stage="QNA",
                event="qna.generate",
                level="INFO",
                message="Grounded Q&A generation completed.",
                metadata={"degraded_mode": answer.degraded_mode},
            )
            self.repo.add_chat_turn(session_id, "user", question)
            self.repo.add_chat_turn(session_id, "assistant", answer.answer)
            elapsed = perf_counter() - started
            self.metrics.qna_latency_seconds.observe(elapsed)
        return {
            "session_id": session_id,
            "correlation_id": correlation_id,
            "question": question,
            "answer": answer.answer,
            "evidence": answer.evidence.model_dump(mode="json"),
            "tool_trace": [
                trace.model_dump(mode="json") for trace in answer.tool_trace
            ],
            "degraded_mode": answer.degraded_mode,
        }

    def recover_inflight_jobs(self) -> int:
        """Re-queue jobs left running after an unclean shutdown.

        Returns:
            Number of jobs that were moved back to the queue.
        """
        recovered = 0
        for job in self.repo.list_recovery_jobs():
            self.repo.set_job_state(
                job.job_id,
                status=JobStatus.queued,
                stage=JobStage.validate_input,
                message="Recovered after worker restart",
            )
            self.repo.log_event(
                job.job_id,
                job.session_id,
                JobStage.validate_input,
                "job.recovered",
                "WARNING",
                "Job re-queued after worker restart.",
            )
            recovered += 1
        self.metrics.queue_depth.set(self.repo.get_queue_depth())
        return recovered

    def update_worker_heartbeat(self, *, mode: str | None = None) -> None:
        """Write the latest worker heartbeat to persistent system state.

        Args:
            mode: Optional explicit worker mode override.
        """
        worker_mode = mode or (
            "embedded" if self.settings.embedded_worker else "standalone"
        )
        self.repo.update_worker_heartbeat({"status": "alive", "mode": worker_mode})

    def readiness(self) -> dict[str, Any]:
        """Compute the readiness payload used by the HTTP probe.

        Returns:
            JSON-serializable readiness payload.
        """
        worker_state = self.repo.get_worker_heartbeat()
        if worker_state is None:
            return {
                "ready": False,
                "reason": "worker heartbeat not seen yet",
                "worker": {
                    "mode": "embedded"
                    if self.settings.embedded_worker
                    else "standalone",
                    "heartbeat_ttl_seconds": self.settings.worker_heartbeat_ttl_s,
                },
                "storage": {"data_dir": str(self.settings.data_dir)},
            }

        updated_at = datetime.fromisoformat(str(worker_state["updated_at"]))
        if updated_at.tzinfo is None:
            updated_at = updated_at.replace(tzinfo=timezone.utc)
        heartbeat_age_seconds = max(0.0, (utcnow() - updated_at).total_seconds())
        ready = heartbeat_age_seconds <= self.settings.worker_heartbeat_ttl_s
        reason = None if ready else "worker heartbeat expired"
        try:
            payload = json.loads(str(worker_state["value"]))
        except json.JSONDecodeError:
            payload = {"status": "unknown"}
        return {
            "ready": ready,
            "reason": reason,
            "worker": {
                "status": payload.get("status", "unknown"),
                "mode": payload.get("mode", "unknown"),
                "heartbeat_age_seconds": round(heartbeat_age_seconds, 3),
                "heartbeat_ttl_seconds": self.settings.worker_heartbeat_ttl_s,
                "updated_at": updated_at.isoformat(),
            },
            "storage": {"data_dir": str(self.settings.data_dir)},
        }
