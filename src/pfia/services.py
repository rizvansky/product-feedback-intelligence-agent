from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from time import perf_counter
from typing import Any

from pfia.analysis import AnalysisArtifacts, analyze_reviews
from pfia.config import Settings, get_settings
from pfia.db import Database, utcnow
from pfia.errors import PFIAError
from pfia.metrics import Metrics
from pfia.models import JobStage, JobStatus, SessionStatus, UploadResponse
from pfia.preprocessing import preprocess_upload, write_sanitized_jsonl
from pfia.qna import answer_question
from pfia.reporting import build_report_markdown, write_report
from pfia.repository import Repository
from pfia.retrieval import build_retrieval_index
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
    settings: Settings
    db: Database
    repo: Repository
    metrics: Metrics


def build_app_context(settings: Settings | None = None) -> AppContext:
    resolved_settings = settings or get_settings()
    resolved_settings.ensure_directories()
    db = Database(resolved_settings.db_path)
    return AppContext(
        settings=resolved_settings,
        db=db,
        repo=Repository(db),
        metrics=Metrics(),
    )


class PFIAService:
    def __init__(self, context: AppContext):
        self.context = context
        self.settings = context.settings
        self.repo = context.repo
        self.metrics = context.metrics

    def upload_file(
        self, filename: str, content: bytes, content_type: str | None = None
    ) -> UploadResponse:
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
            "embedding_backend": self.settings.embedding_backend,
            "generation_backend": self.settings.generation_backend,
            "llm_primary_model": self.settings.llm_primary_model,
            "llm_fallback_model": self.settings.llm_fallback_model,
        }
        self.repo.create_session_and_job(session_id, job_id, config_snapshot)
        self.metrics.queue_depth.set(self.repo.get_queue_depth())
        return UploadResponse(session_id=session_id, job_id=job_id, status="QUEUED")

    def process_next_job(self) -> str | None:
        job_id = self.repo.get_next_queued_job_id()
        if job_id is None:
            self.metrics.queue_depth.set(self.repo.get_queue_depth())
            return None
        self.process_job(job_id)
        self.metrics.queue_depth.set(self.repo.get_queue_depth())
        return job_id

    def process_job(self, job_id: str) -> None:
        job = self.repo.get_job(job_id)
        if job is None:
            raise KeyError(f"Unknown job_id: {job_id}")
        session = self.repo.get_session(job.session_id)
        if session is None:
            raise KeyError(f"Unknown session for job_id: {job_id}")

        started = perf_counter()
        degraded_mode = False

        try:
            self.repo.set_job_state(
                job_id,
                status=JobStatus.running,
                stage=JobStage.validate_input,
                message="Validating upload",
            )
            self.repo.set_session_state(
                session.session_id, status=SessionStatus.processing
            )
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
                raise PFIAError(
                    "FAILED_INPUT", "Uploaded file is missing from storage."
                )

            reviews, summary = self._run_preprocess(
                job_id, session.session_id, upload_path
            )
            analysis = self._run_analysis(job_id, session.session_id, reviews, summary)
            degraded_mode = analysis.degraded_mode
            report_path = self._run_reporting(
                job_id, session.session_id, summary, reviews, analysis, degraded_mode
            )
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
                session.session_id,
                status=final_session_status,
                degraded_mode=degraded_mode,
                report_path=str(report_path),
            )
            self.repo.log_event(
                job_id,
                session.session_id,
                JobStage.finalize,
                "job.finalize",
                "INFO",
                "Job finalized.",
            )
            self.metrics.job_total.labels(status=final_job_status.value).inc()
            if degraded_mode:
                self.metrics.degraded_jobs_total.inc()
        except PFIAError as exc:
            failed_status = FAILED_STATUS_BY_CODE.get(
                exc.code, JobStatus.failed_runtime
            )
            self.repo.set_job_state(
                job_id,
                status=failed_status,
                stage=job.stage,
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
                job.stage,
                "job.failed",
                "ERROR",
                f"{exc.code}: {exc.message}",
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

    def _run_preprocess(self, job_id: str, session_id: str, upload_path: Path):
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
        sanitized_path = self.settings.sanitized_dir / f"{session_id}.jsonl"
        write_sanitized_jsonl(sanitized_path, reviews)
        self.repo.save_preprocessing_summary(session_id, summary)
        self.repo.replace_reviews(session_id, reviews)
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
        return reviews, summary

    def _run_analysis(
        self, job_id: str, session_id: str, reviews, summary
    ) -> AnalysisArtifacts:
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
        session_id: str,
        summary,
        reviews,
        analysis: AnalysisArtifacts,
        degraded_mode: bool,
    ) -> Path:
        self.repo.set_job_state(
            job_id,
            status=JobStatus.degraded_running if degraded_mode else JobStatus.running,
            stage=JobStage.index_for_retrieval,
            degraded_mode=degraded_mode,
            message="Building retrieval index",
        )
        report_markdown, executive_summary = build_report_markdown(
            session_id,
            summary,
            analysis.clusters[: self.settings.report_top_clusters],
            analysis.alerts,
            degraded_mode=degraded_mode,
            diagnostics=analysis.diagnostics,
        )
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
        build_retrieval_index(
            session_id,
            review_payload,
            analysis.clusters,
            report_sections={
                "executive_summary": executive_summary,
                "top_themes": "\n".join(report_markdown.splitlines()[0:40]),
                "alerts": "\n".join(
                    line
                    for line in report_markdown.splitlines()
                    if "Alerts" in line or line.startswith("- ")
                ),
            },
            index_path=self.settings.indexes_dir / f"{session_id}.pkl",
        )
        self.repo.log_event(
            job_id,
            session_id,
            JobStage.index_for_retrieval,
            "job.index.done",
            "INFO",
            "Retrieval index saved.",
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

    def get_session_detail(self, session_id: str) -> dict[str, Any]:
        detail = self.repo.get_session_detail(session_id)
        return {
            **detail.model_dump(mode="json"),
            "events": self.repo.get_job_events(session_id),
        }

    def chat(self, session_id: str, question: str) -> dict[str, Any]:
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
        answer = answer_question(
            self.settings.indexes_dir / f"{session_id}.pkl", session_ready, question
        )
        self.repo.add_chat_turn(session_id, "user", question)
        self.repo.add_chat_turn(session_id, "assistant", answer.answer)
        elapsed = perf_counter() - started
        self.metrics.qna_latency_seconds.observe(elapsed)
        return {
            "session_id": session_id,
            "question": question,
            "answer": answer.answer,
            "evidence": answer.evidence.model_dump(mode="json"),
            "tool_trace": [
                trace.model_dump(mode="json") for trace in answer.tool_trace
            ],
            "degraded_mode": answer.degraded_mode,
        }

    def recover_inflight_jobs(self) -> int:
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
        worker_mode = mode or (
            "embedded" if self.settings.embedded_worker else "standalone"
        )
        self.repo.update_worker_heartbeat({"status": "alive", "mode": worker_mode})

    def readiness(self) -> dict[str, Any]:
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
