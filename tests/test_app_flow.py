from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from pfia.api import create_app
from pfia.config import Settings
from pfia.models import JobStage, JobStatus
from pfia.services import PFIAService


def test_smoke_batch_flow(app, demo_file_path):
    """Verify that an uploaded batch completes end-to-end successfully."""
    with TestClient(app) as client:
        with demo_file_path.open("rb") as handle:
            response = client.post(
                "/api/sessions/upload",
                files={"file": ("mobile_app_reviews.csv", handle, "text/csv")},
            )
        assert response.status_code == 200
        payload = response.json()

        service: PFIAService = app.state.service
        service.process_job(payload["job_id"])

        session_response = client.get(f"/api/sessions/{payload['session_id']}")
        assert session_response.status_code == 200
        session_payload = session_response.json()
        assert session_payload["session"]["status"] == "COMPLETED"
        assert session_payload["job"]["status"] == "COMPLETED"
        assert session_payload["report"]["markdown"].startswith("# PFIA Report")
        assert "## Runtime Metadata" in session_payload["report"]["markdown"]
        assert len(session_payload["clusters"]) >= 5
        assert session_payload["runtime_metadata"]["runtime_profile"] == "deterministic"
        assert (
            session_payload["runtime_metadata"]["orchestrator_backend_effective"]
            == "langgraph"
        )
        assert (
            session_payload["runtime_metadata"]["generation_backend_effective"]
            == "local"
        )
        assert (
            session_payload["runtime_metadata"]["retrieval_backend_effective"]
            == "chroma"
        )
        assert (
            session_payload["runtime_metadata"]["pii_backend_requested"]
            == "regex+spacy"
        )
        assert session_payload["runtime_metadata"]["pii_backend_effective"] in {
            "regex",
            "regex+spacy",
        }
        assert session_payload["runtime_metadata"]["sentiment_backend_requested"] == (
            "vader"
        )
        assert (
            session_payload["runtime_metadata"]["sentiment_backend_effective"] != "n/a"
        )
        assert (
            session_payload["runtime_metadata"]["input_filename"]
            == "mobile_app_reviews.csv"
        )
        assert (
            session_payload["runtime_metadata"]["embedding_backend_effective"]
            == "projection"
        )
        assert "taxonomy_agent" in session_payload["runtime_metadata"]["agent_usage"]


def test_privacy_masking_in_sanitized_artifacts_and_report(app, demo_file_path):
    """Verify that PII is masked in persisted artifacts and reports."""
    service: PFIAService = app.state.service
    upload = service.upload_file(
        "mobile_app_reviews.csv", demo_file_path.read_bytes(), "text/csv"
    )
    service.process_job(upload.job_id)

    sanitized_path = service.settings.sanitized_dir / f"{upload.session_id}.jsonl"
    report_path = service.settings.reports_dir / f"{upload.session_id}.md"

    sanitized_content = sanitized_path.read_text(encoding="utf-8")
    report_content = report_path.read_text(encoding="utf-8")

    assert "anna.peterson@example.com" not in sanitized_content
    assert "+7 999 123 45 67" not in sanitized_content
    assert "[EMAIL]" in sanitized_content
    assert "[PHONE]" in sanitized_content
    assert "anna.peterson@example.com" not in report_content
    assert "+7 999 123 45 67" not in report_content


def test_recovery_requeues_running_job(app, demo_file_path):
    """Verify that recovery moves interrupted jobs back into the queue."""
    service: PFIAService = app.state.service
    upload = service.upload_file(
        "mobile_app_reviews.csv", demo_file_path.read_bytes(), "text/csv"
    )
    service.repo.set_job_state(
        upload.job_id,
        status=JobStatus.running,
        stage=JobStage.cluster,
        message="Simulated crash",
    )

    recovered = service.recover_inflight_jobs()
    assert recovered == 1
    job = service.repo.get_job(upload.job_id)
    assert job is not None
    assert job.status == JobStatus.queued
    assert job.stage == JobStage.validate_input

    service.process_next_job()
    detail = service.get_session_detail(upload.session_id)
    assert detail["session"]["status"] == "COMPLETED"


def test_priority_question_returns_grounded_top_issue(app, demo_file_path):
    """Verify that priority Q&A selects the top grounded issue."""
    service: PFIAService = app.state.service
    upload = service.upload_file(
        "mobile_app_reviews.csv", demo_file_path.read_bytes(), "text/csv"
    )
    service.process_job(upload.job_id)

    answer = service.chat(
        upload.session_id,
        "What is the highest-priority issue and what evidence supports it?",
    )

    assert "Payment flow crashes" in answer["answer"]
    assert "payment_flow_crashes" in answer["answer"]
    assert any(
        trace["tool"] in {"top_clusters", "search_clusters"}
        for trace in answer["tool_trace"]
    )
    assert answer["evidence"]["cluster_hits"][0]["cluster_id"].startswith(
        "payment_flow_crashes"
    )


def test_railway_hosting_defaults(monkeypatch):
    """Verify that Railway-specific environment defaults are applied."""
    monkeypatch.setenv("PORT", "9001")
    monkeypatch.setenv("RAILWAY_VOLUME_MOUNT_PATH", "/data")

    settings = Settings(_env_file=None)

    assert settings.port == 9001
    assert settings.embedded_worker is True
    assert settings.data_dir == Path("/data/runtime")


def test_embedded_worker_readiness_uses_background_heartbeat(tmp_path):
    """Verify that embedded worker mode satisfies the readiness probe."""
    settings = Settings(
        data_dir=tmp_path / "runtime",
        embedded_worker=True,
        _env_file=None,
    )
    app = create_app(settings)
    service: PFIAService = app.state.service
    service.update_worker_heartbeat(mode="embedded")

    payload = service.readiness()
    assert payload["ready"] is True
    assert payload["worker"]["mode"] == "embedded"
    assert payload["storage"]["data_dir"].endswith("/runtime")


def test_linear_orchestrator_fallback_still_completes(tmp_path, demo_file_path):
    """Verify that the legacy linear orchestrator remains a safe fallback."""
    settings = Settings(
        data_dir=tmp_path / "runtime",
        generation_backend="local",
        embedding_backend="local",
        retrieval_backend="local",
        orchestrator_backend="linear",
        openai_api_key="",
        _env_file=None,
    )
    app = create_app(settings)
    service: PFIAService = app.state.service
    upload = service.upload_file(
        "mobile_app_reviews.csv", demo_file_path.read_bytes(), "text/csv"
    )
    service.process_job(upload.job_id)

    detail = service.get_session_detail(upload.session_id)
    assert detail["session"]["status"] == "COMPLETED"
    assert detail["runtime_metadata"]["orchestrator_backend_effective"] == "linear"
    assert detail["runtime_metadata"]["retrieval_backend_effective"] == "local"
    assert detail["runtime_metadata"]["pii_backend_requested"] == "regex+spacy"
