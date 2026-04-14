from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any

from pfia.config import Settings
from pfia.models import JobStage, JobStatus
from pfia.services import PFIAService, build_app_context


EMAIL_PATTERN = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
PHONE_PATTERN = re.compile(r"\+?\d[\d\-\s()]{7,}\d")

DEFAULT_ACCEPTANCE_QUESTIONS = [
    {
        "question": "What is the highest-priority issue and what evidence supports it?",
        "expected_prefix": "payment_flow_crashes",
    },
    {
        "question": "Which payment-related topic is spiking this week?",
        "expected_prefix": "payment_flow_crashes",
    },
    {
        "question": "What login-related issue appears in this batch?",
        "expected_prefix": "login_code_delays",
    },
]


def _load_service(settings: Settings) -> PFIAService:
    """Create an isolated service instance for acceptance evaluations."""

    return PFIAService(build_app_context(settings))


def _run_batch(service: PFIAService, dataset_path: Path) -> tuple[str, dict[str, Any]]:
    """Upload and process one dataset end-to-end for evals."""

    upload = service.upload_file(
        dataset_path.name,
        dataset_path.read_bytes(),
        "application/json" if dataset_path.suffix.lower() == ".json" else "text/csv",
    )
    service.process_job(upload.job_id)
    detail = service.get_session_detail(upload.session_id)
    return upload.session_id, detail


def _check_no_pii(text: str) -> bool:
    """Return whether a text blob appears free of obvious raw email/phone PII."""

    return not EMAIL_PATTERN.search(text) and not PHONE_PATTERN.search(text)


def run_acceptance_evals(
    *,
    settings: Settings,
    dataset_path: Path,
) -> dict[str, Any]:
    """Run proposal-aligned acceptance checks and return a structured summary."""

    service = _load_service(settings)
    session_id, detail = _run_batch(service, dataset_path)
    checks: list[dict[str, Any]] = []

    session_status = detail["session"]["status"]
    job_status = detail["job"]["status"]
    report_markdown = (detail.get("report") or {}).get("markdown") or ""
    runtime_metadata = detail.get("runtime_metadata") or {}
    events = detail.get("events") or []

    checks.append(
        {
            "name": "batch_flow_completed",
            "passed": session_status in {"COMPLETED", "DEGRADED_COMPLETED"}
            and job_status == "COMPLETED",
            "details": {
                "session_status": session_status,
                "job_status": job_status,
            },
        }
    )
    checks.append(
        {
            "name": "runtime_metadata_present",
            "passed": bool(runtime_metadata)
            and runtime_metadata.get("retrieval_backend_effective") is not None,
            "details": {
                "runtime_profile": runtime_metadata.get("runtime_profile"),
                "retrieval_backend_effective": runtime_metadata.get(
                    "retrieval_backend_effective"
                ),
                "orchestrator_backend_effective": runtime_metadata.get(
                    "orchestrator_backend_effective"
                ),
            },
        }
    )
    checks.append(
        {
            "name": "trace_metadata_present",
            "passed": bool(runtime_metadata.get("trace_correlation_id"))
            and runtime_metadata.get("trace_correlation_id") != "n/a"
            and bool(runtime_metadata.get("trace_exporters_effective")),
            "details": {
                "trace_correlation_id": runtime_metadata.get("trace_correlation_id"),
                "trace_exporters_effective": runtime_metadata.get(
                    "trace_exporters_effective"
                ),
            },
        }
    )
    checks.append(
        {
            "name": "report_contains_runtime_section",
            "passed": "## Runtime Metadata" in report_markdown,
            "details": {},
        }
    )

    sanitized_path = service.settings.sanitized_dir / f"{session_id}.jsonl"
    sanitized_rows = [
        json.loads(line)
        for line in sanitized_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    sanitized_text = "\n".join(
        str(row.get("text_anonymized") or "") for row in sanitized_rows
    )
    report_safe_text = report_markdown.split("## Runtime Metadata", maxsplit=1)[0]
    checks.append(
        {
            "name": "pii_masking_regression",
            "passed": _check_no_pii(sanitized_text) and _check_no_pii(report_safe_text),
            "details": {
                "sanitized_path": str(sanitized_path),
            },
        }
    )
    checks.append(
        {
            "name": "event_correlation_ids_present",
            "passed": bool(events)
            and all((event.get("correlation_id") or "").strip() for event in events),
            "details": {
                "event_count": len(events),
            },
        }
    )

    retrieval_results = []
    retrieval_passed = True
    for item in DEFAULT_ACCEPTANCE_QUESTIONS:
        answer = service.chat(session_id, item["question"])
        top_hits = answer["evidence"]["cluster_hits"]
        top_cluster_id = top_hits[0]["cluster_id"] if top_hits else ""
        passed = top_cluster_id.startswith(item["expected_prefix"])
        retrieval_passed = retrieval_passed and passed
        retrieval_results.append(
            {
                "question": item["question"],
                "expected_prefix": item["expected_prefix"],
                "top_cluster_id": top_cluster_id,
                "degraded_mode": answer.get("degraded_mode"),
                "passed": passed,
            }
        )
    checks.append(
        {
            "name": "retrieval_eval_fixed_questions",
            "passed": retrieval_passed,
            "details": {"questions": retrieval_results},
        }
    )

    recovery_service = _load_service(
        Settings(
            data_dir=service.settings.data_dir.parent / "eval-recovery-runtime",
            generation_backend=settings.generation_backend,
            embedding_backend=settings.embedding_backend,
            retrieval_backend=settings.retrieval_backend,
            orchestrator_backend=settings.orchestrator_backend,
            chroma_mode=settings.chroma_mode,
            chroma_host=settings.chroma_host,
            chroma_port=settings.chroma_port,
            chroma_ssl=settings.chroma_ssl,
            openai_api_key=settings.openai_api_key,
            mistral_api_key=settings.mistral_api_key,
            anthropic_api_key=settings.anthropic_api_key,
            _env_file=None,
        )
    )
    recovery_upload = recovery_service.upload_file(
        dataset_path.name,
        dataset_path.read_bytes(),
        "application/json" if dataset_path.suffix.lower() == ".json" else "text/csv",
    )
    recovery_service.repo.set_job_state(
        recovery_upload.job_id,
        status=JobStatus.running,
        stage=JobStage.cluster,
        message="Simulated crash during acceptance eval.",
    )
    recovered = recovery_service.recover_inflight_jobs()
    recovery_service.process_next_job()
    recovery_detail = recovery_service.get_session_detail(recovery_upload.session_id)
    checks.append(
        {
            "name": "recovery_eval",
            "passed": recovered == 1
            and recovery_detail["session"]["status"] == "COMPLETED",
            "details": {
                "recovered_jobs": recovered,
                "session_status": recovery_detail["session"]["status"],
            },
        }
    )

    passed = all(check["passed"] for check in checks)
    return {
        "dataset": str(dataset_path),
        "passed": passed,
        "check_count": len(checks),
        "checks": checks,
    }


def main() -> None:
    """CLI entrypoint for proposal-aligned acceptance evals."""

    parser = argparse.ArgumentParser(description="Run PFIA acceptance evals.")
    parser.add_argument(
        "--dataset",
        default="data/demo/mobile_app_reviews.csv",
        help="Path to CSV/JSON dataset used for acceptance evals.",
    )
    args = parser.parse_args()

    settings = Settings(_env_file=".env")
    result = run_acceptance_evals(
        settings=settings,
        dataset_path=Path(args.dataset),
    )
    print(json.dumps(result, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
