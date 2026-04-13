from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from pfia.api import create_app
from pfia.config import Settings
from pfia.models import JobStage, JobStatus
from pfia.services import PFIAService


def test_smoke_batch_flow(app, demo_file_path):
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
        assert len(session_payload["clusters"]) >= 5


def test_privacy_masking_in_sanitized_artifacts_and_report(app, demo_file_path):
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
    assert any(trace["tool"] == "search_clusters" for trace in answer["tool_trace"])
    assert answer["evidence"]["cluster_hits"][0]["cluster_id"].startswith(
        "payment_flow_crashes"
    )


def test_railway_hosting_defaults(monkeypatch):
    monkeypatch.setenv("PORT", "9001")
    monkeypatch.setenv("RAILWAY_VOLUME_MOUNT_PATH", "/data")

    settings = Settings()

    assert settings.port == 9001
    assert settings.embedded_worker is True
    assert settings.data_dir == Path("/data/runtime")


def test_embedded_worker_readiness_uses_background_heartbeat(tmp_path):
    settings = Settings(
        data_dir=tmp_path / "runtime", embedded_worker=True, worker_poll_interval_s=0.05
    )
    app = create_app(settings)

    with TestClient(app) as client:
        readiness = client.get("/health/ready")

    assert readiness.status_code == 200
    payload = readiness.json()
    assert payload["ready"] is True
    assert payload["worker"]["mode"] == "embedded"
    assert payload["storage"]["data_dir"].endswith("/runtime")
