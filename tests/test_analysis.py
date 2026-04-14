from __future__ import annotations

from datetime import datetime, timezone
import types

import numpy as np

from pfia.analysis import (
    _cluster_texts,
    analyze_reviews,
    partition_clusters_for_display,
)
from pfia.config import Settings
from pfia.models import ReviewNormalized


def test_hdbscan_reflection_retries_until_quality_gate(monkeypatch):
    """Verify that HDBSCAN reflection tries multiple profiles until the gate passes."""

    monkeypatch.setattr(
        "pfia.analysis._build_clustering_embeddings",
        lambda texts, settings: (
            np.asarray(
                [
                    [1.0, 0.0, 0.0],
                    [0.9, 0.1, 0.0],
                    [0.0, 1.0, 0.0],
                    [0.0, 0.9, 0.1],
                    [0.0, 0.0, 1.0],
                    [0.1, 0.0, 0.9],
                ],
                dtype=np.float32,
            ),
            {
                "embedding_backend_effective": "projection",
                "embedding_model_effective": "tfidf-svd-projection",
                "embedding_degraded_reason": None,
            },
        ),
    )

    quality_by_signature = {
        (0, 0, 1, 1, -1, -1): 0.12,
        (0, 0, 1, 1, 2, -1): 0.24,
        (0, 0, 1, 1, 2, 2): 0.41,
    }

    monkeypatch.setattr(
        "pfia.analysis._quality_score",
        lambda embeddings, labels: quality_by_signature[
            tuple(int(label) for label in labels)
        ],
    )

    class FakeHDBSCAN:
        """Fake HDBSCAN implementation keyed by profile params."""

        def __init__(self, *, min_cluster_size, min_samples, metric):
            _ = metric
            self.min_cluster_size = min_cluster_size
            self.min_samples = min_samples

        def fit_predict(self, embeddings):
            _ = embeddings
            if self.min_cluster_size == 3 and self.min_samples == 1:
                return np.array([0, 0, 1, 1, -1, -1], dtype=int)
            if self.min_cluster_size == 2 and self.min_samples == 1:
                return np.array([0, 0, 1, 1, 2, -1], dtype=int)
            return np.array([0, 0, 1, 1, 2, 2], dtype=int)

    monkeypatch.setattr(
        "pfia.analysis.hdbscan",
        types.SimpleNamespace(HDBSCAN=FakeHDBSCAN),
    )
    monkeypatch.setattr(
        "pfia.analysis._agglomerative_profile",
        lambda embeddings: (
            np.array([0, 0, 0, 1, 1, 1], dtype=int),
            0.18,
            [],
        ),
    )

    settings = Settings(
        generation_backend="local",
        embedding_backend="local",
        clustering_min_cluster_size=3,
        clustering_min_samples=1,
        clustering_reflection_threshold=0.35,
        clustering_reflection_max_profiles=3,
        clustering_max_cluster_count=20,
        _env_file=None,
    )

    labels, quality, metadata = _cluster_texts(
        [
            "payment crash",
            "checkout crash",
            "otp delay",
            "verification code slow",
            "refund issue",
            "double charge",
        ],
        settings,
    )

    assert quality == 0.41
    assert labels.tolist() == [0, 0, 1, 1, 2, 2]
    assert metadata["clustering_backend_effective"] == "hdbscan"
    assert metadata["clustering_selected_profile"] == "hdbscan_more_stable"
    assert metadata["clustering_reflection_triggered"] is True
    assert metadata["clustering_reflection_attempt_count"] == 3
    assert metadata["clustering_quality_gate_passed"] is True
    assert len(metadata["clustering_attempts"]) == 3
    assert metadata["clustering_attempts"][-1]["accepted"] is True


def test_clustering_falls_back_to_agglomerative_when_hdbscan_unavailable(monkeypatch):
    """Verify that clustering metadata records the agglomerative fallback."""

    monkeypatch.setattr(
        "pfia.analysis._build_clustering_embeddings",
        lambda texts, settings: (
            np.asarray(
                [
                    [1.0, 0.0],
                    [0.9, 0.1],
                    [0.0, 1.0],
                    [0.1, 0.9],
                ],
                dtype=np.float32,
            ),
            {
                "embedding_backend_effective": "projection",
                "embedding_model_effective": "tfidf-svd-projection",
                "embedding_degraded_reason": None,
            },
        ),
    )
    monkeypatch.setattr("pfia.analysis.hdbscan", None)
    monkeypatch.setattr(
        "pfia.analysis._agglomerative_profile",
        lambda embeddings: (
            np.array([0, 0, 1, 1], dtype=int),
            0.38,
            [],
        ),
    )

    settings = Settings(
        generation_backend="local",
        embedding_backend="local",
        clustering_reflection_threshold=0.35,
        _env_file=None,
    )
    labels, quality, metadata = _cluster_texts(
        ["payment crash", "checkout crash", "otp delay", "verification code slow"],
        settings,
    )

    assert quality == 0.38
    assert labels.tolist() == [0, 0, 1, 1]
    assert metadata["clustering_backend_effective"] == "agglomerative"
    assert metadata["clustering_quality_gate_passed"] is True


def test_analysis_surfaces_weak_signals_and_mixed_sentiment(monkeypatch):
    """Verify display partitioning matches proposal edge-case handling."""

    monkeypatch.setattr(
        "pfia.analysis._cluster_texts",
        lambda texts, settings: (
            np.array([0, 0, 1, 1, 1], dtype=int),
            0.42,
            {
                "clustering_backend_effective": "hdbscan",
                "clustering_selected_profile": "hdbscan_default",
                "clustering_reflection_attempt_count": 1,
                "clustering_quality_gate_passed": True,
                "clustering_cluster_count_gate_passed": True,
                "clustering_attempts": [],
                "embedding_backend_effective": "projection",
                "embedding_model_effective": "tfidf-svd-projection",
                "embedding_degraded_reason": None,
            },
        ),
    )

    reviews = [
        ReviewNormalized(
            review_id="r1",
            session_id="sess_test",
            source="app_store",
            created_at=datetime(2026, 4, 1, tzinfo=timezone.utc),
            language="en",
            text_normalized="Payment crash on checkout.",
            text_anonymized="Payment crash on checkout.",
            dedupe_hash="1",
            flags=[],
            metadata={},
        ),
        ReviewNormalized(
            review_id="r2",
            session_id="sess_test",
            source="app_store",
            created_at=datetime(2026, 4, 2, tzinfo=timezone.utc),
            language="en",
            text_normalized="Checkout crash again.",
            text_anonymized="Checkout crash again.",
            dedupe_hash="2",
            flags=[],
            metadata={},
        ),
        ReviewNormalized(
            review_id="r3",
            session_id="sess_test",
            source="google_play",
            created_at=datetime(2026, 4, 3, tzinfo=timezone.utc),
            language="en",
            text_normalized="Support was great and the UI feels smooth.",
            text_anonymized="Support was great and the UI feels smooth.",
            dedupe_hash="3",
            flags=[],
            metadata={},
        ),
        ReviewNormalized(
            review_id="r4",
            session_id="sess_test",
            source="google_play",
            created_at=datetime(2026, 4, 4, tzinfo=timezone.utc),
            language="en",
            text_normalized="The upgrade flow is terrible and confusing.",
            text_anonymized="The upgrade flow is terrible and confusing.",
            dedupe_hash="4",
            flags=[],
            metadata={},
        ),
        ReviewNormalized(
            review_id="r5",
            session_id="sess_test",
            source="google_play",
            created_at=datetime(2026, 4, 5, tzinfo=timezone.utc),
            language="mixed",
            text_normalized="После апдейта стало лучше but billing still annoys me.",
            text_anonymized="После апдейта стало лучше but billing still annoys me.",
            dedupe_hash="5",
            flags=["mixed_language"],
            metadata={"mixed_language_processed": True},
        ),
    ]

    settings = Settings(
        generation_backend="local",
        embedding_backend="local",
        low_data_review_threshold=3,
        weak_signal_max_cluster_size=2,
        _env_file=None,
    )
    analysis = analyze_reviews("sess_test", reviews, settings)
    top_clusters, weak_signals = partition_clusters_for_display(
        analysis.clusters,
        analysis.diagnostics["weak_signal_cluster_ids"],
    )

    assert weak_signals
    assert all(cluster.size <= 2 for cluster in weak_signals)
    assert top_clusters
    assert analysis.diagnostics["mixed_sentiment_cluster_count"] >= 1
    assert analysis.diagnostics["mixed_language_review_count"] == 1
