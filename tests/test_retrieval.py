from __future__ import annotations


import numpy as np

from pfia.config import Settings
from pfia.embeddings import EmbeddingBatchResult
from pfia.models import ClusterRecord
from pfia.retrieval import SessionRetriever, build_retrieval_index


def test_chroma_retrieval_build_and_search(tmp_path):
    """Verify that Chroma-backed retrieval can index and query one session."""
    index_path = tmp_path / "indexes" / "sess_demo.pkl"
    result = build_retrieval_index(
        "sess_demo",
        [
            {
                "review_id": "r1",
                "cluster_id": "payment_flow_crashes_1",
                "text": "Payment flow crashes during checkout.",
                "source": "app_store",
                "created_at": "2026-04-10T00:00:00+00:00",
                "sentiment_score": -0.8,
            }
        ],
        [
            ClusterRecord(
                cluster_id="payment_flow_crashes_1",
                label="Payment flow crashes",
                summary="Checkout crashes block successful payments.",
                review_ids=["r1"],
                top_quote_ids=["r1"],
                priority_score=0.9,
                sentiment_score=-0.8,
                trend_delta=2.0,
                confidence="high",
                keywords=["payment", "checkout", "crash"],
                sources=["app_store"],
                size=1,
            )
        ],
        report_sections={"executive_summary": "Payments are failing."},
        index_path=index_path,
        retrieval_backend="chroma",
        chroma_path=tmp_path / "indexes" / "chroma",
    )

    assert result.effective_backend == "chroma"
    assert result.embedding_backend_effective == "projection"
    retriever = SessionRetriever.load(index_path)
    hits = retriever.search_clusters("Why do payments crash?", top_k=3)
    assert hits
    assert hits[0].cluster_id == "payment_flow_crashes_1"
    assert retriever.get_report_section("executive_summary") == "Payments are failing."


def test_retrieval_falls_back_to_local_when_chroma_is_not_requested(tmp_path):
    """Verify that local retrieval stays functional without Chroma."""
    index_path = tmp_path / "indexes" / "sess_demo.pkl"
    result = build_retrieval_index(
        "sess_demo",
        [
            {
                "review_id": "r1",
                "cluster_id": "login_code_delays_2",
                "text": "Login code arrives too late.",
                "source": "google_play",
                "created_at": "2026-04-10T00:00:00+00:00",
                "sentiment_score": -0.5,
            }
        ],
        [
            ClusterRecord(
                cluster_id="login_code_delays_2",
                label="Login code delays",
                summary="Users wait too long for OTP codes.",
                review_ids=["r1"],
                top_quote_ids=["r1"],
                priority_score=0.6,
                sentiment_score=-0.5,
                trend_delta=1.0,
                confidence="medium",
                keywords=["login", "otp", "code"],
                sources=["google_play"],
                size=1,
            )
        ],
        report_sections={"executive_summary": "OTP delivery is slow."},
        index_path=index_path,
        retrieval_backend="local",
        chroma_path=tmp_path / "indexes" / "chroma",
    )

    assert result.effective_backend == "local"
    retriever = SessionRetriever.load(index_path)
    hits = retriever.search_clusters("Which issue affects OTP codes?", top_k=3)
    assert hits
    assert hits[0].cluster_id == "login_code_delays_2"


def test_retrieval_records_external_embedding_backend(tmp_path, monkeypatch):
    """Verify that retrieval metadata captures an external embedding backend."""

    def fake_embed_texts(texts, settings, **kwargs):
        _ = settings, kwargs
        return EmbeddingBatchResult(
            vectors=np.tile(
                np.array([[1.0, 0.0, 0.0]], dtype=np.float32), (len(texts), 1)
            ),
            backend_requested="openai",
            backend_effective="openai",
            model_effective="text-embedding-3-small",
        )

    monkeypatch.setattr("pfia.retrieval.embed_texts", fake_embed_texts)

    settings = Settings(
        data_dir=tmp_path / "runtime",
        embedding_backend="openai",
        openai_api_key="test-key",
        _env_file=None,
    )
    index_path = tmp_path / "indexes" / "sess_demo.pkl"
    result = build_retrieval_index(
        "sess_demo",
        [
            {
                "review_id": "r1",
                "cluster_id": "payment_flow_crashes_1",
                "text": "Payment flow crashes during checkout.",
                "source": "app_store",
                "created_at": "2026-04-10T00:00:00+00:00",
                "sentiment_score": -0.8,
            }
        ],
        [
            ClusterRecord(
                cluster_id="payment_flow_crashes_1",
                label="Payment flow crashes",
                summary="Checkout crashes block successful payments.",
                review_ids=["r1"],
                top_quote_ids=["r1"],
                priority_score=0.9,
                sentiment_score=-0.8,
                trend_delta=2.0,
                confidence="high",
                keywords=["payment", "checkout", "crash"],
                sources=["app_store"],
                size=1,
            )
        ],
        report_sections={"executive_summary": "Payments are failing."},
        index_path=index_path,
        settings=settings,
        retrieval_backend="chroma",
        chroma_path=tmp_path / "indexes" / "chroma",
    )

    assert result.effective_backend == "chroma"
    assert result.embedding_backend_effective == "openai"
    assert result.embedding_model_effective == "text-embedding-3-small"

    retriever = SessionRetriever.load(index_path, settings=settings)
    hits = retriever.search_clusters("What is failing during payment?", top_k=3)
    assert hits
    assert hits[0].cluster_id == "payment_flow_crashes_1"
