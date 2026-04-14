from __future__ import annotations

from pfia.config import Settings
from pfia.evals import run_acceptance_evals


def test_acceptance_evals_pass_on_demo_dataset(tmp_path, demo_file_path):
    """Verify that the automated acceptance harness passes on the demo dataset."""

    settings = Settings(
        data_dir=tmp_path / "runtime",
        generation_backend="local",
        embedding_backend="local",
        retrieval_backend="chroma",
        orchestrator_backend="langgraph",
        _env_file=None,
    )

    result = run_acceptance_evals(
        settings=settings,
        dataset_path=demo_file_path,
    )

    assert result["passed"] is True
    checks = {item["name"]: item["passed"] for item in result["checks"]}
    assert checks["batch_flow_completed"] is True
    assert checks["pii_masking_regression"] is True
    assert checks["retrieval_eval_fixed_questions"] is True
    assert checks["recovery_eval"] is True
