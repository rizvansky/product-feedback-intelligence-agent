from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from statistics import mean, pstdev
from typing import Any

import numpy as np
from scipy.sparse import hstack
from sklearn.cluster import AgglomerativeClustering
from sklearn.decomposition import TruncatedSVD
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics import silhouette_score
from sklearn.preprocessing import Normalizer

from pfia.config import Settings
from pfia.models import AlertRecord, ClusterRecord, ReviewNormalized
from pfia.utils import normalize_text, slugify, tokenize

try:
    import hdbscan
except ImportError:  # pragma: no cover - optional dependency in local dev only
    hdbscan = None


POSITIVE_WORDS = {
    "good",
    "great",
    "love",
    "fast",
    "stable",
    "smooth",
    "helpful",
    "excellent",
    "amazing",
    "удобно",
    "отлично",
    "круто",
    "быстро",
    "нравится",
    "полезно",
    "стабильно",
}

NEGATIVE_WORDS = {
    "bad",
    "broken",
    "bug",
    "bugs",
    "crash",
    "crashes",
    "slow",
    "hate",
    "problem",
    "annoying",
    "terrible",
    "ошибка",
    "плохо",
    "медленно",
    "лагает",
    "сломано",
    "вылетает",
    "ужасно",
    "баг",
}

STOPWORDS = {
    "a",
    "an",
    "and",
    "are",
    "as",
    "at",
    "be",
    "but",
    "by",
    "for",
    "from",
    "i",
    "if",
    "in",
    "is",
    "it",
    "me",
    "my",
    "of",
    "on",
    "or",
    "the",
    "this",
    "to",
    "too",
    "was",
    "with",
    "after",
    "again",
    "still",
    "every",
    "very",
    "latest",
    "please",
    "person",
    "email",
    "phone",
    "url",
    "device",
    "device_id",
    "бы",
    "в",
    "во",
    "для",
    "до",
    "же",
    "за",
    "и",
    "из",
    "как",
    "когда",
    "мне",
    "на",
    "не",
    "но",
    "о",
    "по",
    "после",
    "при",
    "с",
    "со",
    "слишком",
    "нужен",
    "у",
    "это",
    "очень",
}

CONCEPT_DEFINITIONS = {
    "payment_crash": {
        "label": "Payment flow crashes",
        "patterns": [
            ("crash", "payment"),
            ("crash", "checkout"),
            ("вылет", "оплат"),
            ("вылетает", "оплат"),
            ("paywall", "crash"),
            ("payment", "screen"),
            ("экране", "карты"),
        ],
    },
    "login_otp": {
        "label": "Login code delays",
        "patterns": [
            ("login", "code"),
            ("sign", "code"),
            ("otp",),
            ("verification", "code"),
            ("код", "вход"),
            ("код", "подтверж"),
            ("sms", "code"),
        ],
    },
    "billing_refund": {
        "label": "Billing and refunds",
        "patterns": [
            ("subscription",),
            ("refund",),
            ("billing",),
            ("charged",),
            ("подпис",),
            ("возврат",),
            ("дважды",),
        ],
    },
    "dark_mode": {
        "label": "Dark mode requests",
        "patterns": [
            ("dark", "mode"),
            ("dark", "theme"),
            ("темн", "режим"),
            ("темн", "тема"),
        ],
    },
    "notifications": {
        "label": "Notification delivery",
        "patterns": [
            ("notification",),
            ("notifications",),
            ("уведомлен",),
        ],
    },
    "performance_praise": {
        "label": "Positive UX feedback",
        "patterns": [
            ("onboarding",),
            ("home", "screen"),
            ("главн", "экран"),
            ("stable",),
            ("стабиль",),
            ("быстр",),
            ("smooth",),
        ],
    },
    "support_resolution": {
        "label": "Support resolution feedback",
        "patterns": [
            ("support",),
            ("resolved",),
            ("email",),
            ("поддерж",),
        ],
    },
}


@dataclass
class AnalysisArtifacts:
    """Bundle of outputs produced by the local analysis pipeline."""

    clusters: list[ClusterRecord]
    alerts: list[AlertRecord]
    sentiment_by_review: dict[str, float]
    cluster_by_review: dict[str, str]
    diagnostics: dict[str, Any]
    degraded_mode: bool


def analyze_reviews(
    session_id: str, reviews: list[ReviewNormalized], settings: Settings
) -> AnalysisArtifacts:
    """Run clustering, scoring, and alert generation for sanitized reviews.

    Args:
        session_id: Owning session identifier.
        reviews: Sanitized review records.
        settings: Runtime settings controlling clustering behavior.

    Returns:
        Analysis artifact bundle used by reporting and retrieval.
    """
    concepts_by_review = {
        review.review_id: detect_concepts(review.text_anonymized) for review in reviews
    }
    texts = [
        enriched_text(review.text_anonymized, concepts_by_review[review.review_id])
        for review in reviews
    ]
    sentiment_by_review = {
        review.review_id: compute_sentiment(review.text_anonymized)
        for review in reviews
    }

    if len(reviews) < settings.clustering_min_cluster_size:
        labels = np.zeros(len(reviews), dtype=int)
        quality = 0.0
        degraded_mode = True
    else:
        labels, quality = _cluster_texts(texts, settings)
        degraded_mode = quality < settings.clustering_similarity_threshold

    temp_cluster_by_review, grouped = _group_labels(reviews, labels)
    cluster_details, temp_to_final_cluster = _build_clusters(
        session_id=session_id,
        reviews=reviews,
        grouped=grouped,
        sentiment_by_review=sentiment_by_review,
        concepts_by_review=concepts_by_review,
        degraded_mode=degraded_mode,
        top_n=settings.report_top_clusters,
    )
    cluster_by_review = {
        review_id: temp_to_final_cluster[temp_cluster_id]
        for review_id, temp_cluster_id in temp_cluster_by_review.items()
        if temp_cluster_id in temp_to_final_cluster
    }
    alerts = _build_alerts(cluster_details, reviews)
    alert_cluster_ids = {
        alert.cluster_id for alert in alerts if not alert.insufficient_history
    }
    enriched_clusters = [
        cluster.model_copy(
            update={"anomaly_flag": cluster.cluster_id in alert_cluster_ids}
        )
        for cluster in cluster_details
    ]

    return AnalysisArtifacts(
        clusters=enriched_clusters,
        alerts=alerts,
        sentiment_by_review=sentiment_by_review,
        cluster_by_review=cluster_by_review,
        diagnostics={
            "quality_score": quality,
            "total_clusters": len(enriched_clusters),
            "degraded_reason": "low_clustering_quality" if degraded_mode else None,
        },
        degraded_mode=degraded_mode,
    )


def compute_sentiment(text: str) -> float:
    """Estimate sentiment on a simple bounded scale using lexical heuristics.

    Args:
        text: Review text.

    Returns:
        Sentiment score in the ``[-1.0, 1.0]`` range.
    """
    tokens = tokenize(text)
    if not tokens:
        return 0.0
    positive = sum(1 for token in tokens if token in POSITIVE_WORDS)
    negative = sum(1 for token in tokens if token in NEGATIVE_WORDS)
    score = (positive - negative) / max(1, len(tokens))
    return float(max(-1.0, min(1.0, score * 4)))


def _cluster_texts(texts: list[str], settings: Settings) -> tuple[np.ndarray, float]:
    """Cluster texts with HDBSCAN when available and a deterministic fallback.

    Args:
        texts: Enriched review texts.
        settings: Runtime clustering configuration.

    Returns:
        Tuple of ``(labels, quality_score)``.
    """
    word_vectorizer = TfidfVectorizer(
        lowercase=True,
        analyzer="word",
        ngram_range=(1, 2),
        token_pattern=r"(?u)\b\w+\b",
        max_features=2500,
        stop_words=sorted(STOPWORDS),
        max_df=0.88,
    )
    char_vectorizer = TfidfVectorizer(
        lowercase=True,
        analyzer="char_wb",
        ngram_range=(3, 5),
        max_features=2500,
    )
    word_matrix = word_vectorizer.fit_transform(texts)
    char_matrix = char_vectorizer.fit_transform(texts)
    combined = hstack([word_matrix, char_matrix])

    n_samples, n_features = combined.shape
    if n_samples < 3 or n_features < 3:
        return np.zeros(n_samples, dtype=int), 0.0

    n_components = max(2, min(64, n_samples - 1, n_features - 1))
    reduced = TruncatedSVD(n_components=n_components, random_state=42).fit_transform(
        combined
    )
    embeddings = Normalizer(copy=False).fit_transform(reduced)

    if hdbscan is not None:
        clusterer = hdbscan.HDBSCAN(
            min_cluster_size=min(
                settings.clustering_min_cluster_size, max(2, n_samples // 4 or 2)
            ),
            min_samples=min(
                settings.clustering_min_samples, max(1, n_samples // 6 or 1)
            ),
            metric="euclidean",
        )
        labels = clusterer.fit_predict(embeddings)
        quality = _quality_score(embeddings, labels)
    else:
        labels = np.zeros(n_samples, dtype=int)
        quality = 0.0

    fallback_labels, fallback_quality = _agglomerative_profile(embeddings)
    if fallback_quality > quality:
        return fallback_labels, fallback_quality
    return labels, quality


def _group_labels(
    reviews: list[ReviewNormalized], labels: np.ndarray
) -> tuple[dict[str, str], dict[str, list[ReviewNormalized]]]:
    """Map raw cluster labels to stable temporary cluster keys.

    Args:
        reviews: Reviews aligned with the label array.
        labels: Raw clustering labels.

    Returns:
        Mapping from review id to temporary cluster id, plus grouped reviews.
    """
    cluster_by_review: dict[str, str] = {}
    grouped: dict[str, list[ReviewNormalized]] = {}
    for index, review in enumerate(reviews):
        raw_label = int(labels[index])
        cluster_key = "weak_signals" if raw_label == -1 else f"cluster_{raw_label + 1}"
        cluster_by_review[review.review_id] = cluster_key
        grouped.setdefault(cluster_key, []).append(review)
    return cluster_by_review, grouped


def _build_clusters(
    session_id: str,
    reviews: list[ReviewNormalized],
    grouped: dict[str, list[ReviewNormalized]],
    sentiment_by_review: dict[str, float],
    concepts_by_review: dict[str, list[str]],
    degraded_mode: bool,
    top_n: int,
) -> tuple[list[ClusterRecord], dict[str, str]]:
    """Build ranked cluster records from grouped reviews.

    Args:
        session_id: Owning session identifier.
        reviews: Full review set for the batch.
        grouped: Reviews grouped by temporary cluster id.
        sentiment_by_review: Precomputed sentiment scores.
        concepts_by_review: Concept tags per review.
        degraded_mode: Whether the run is already considered degraded.
        top_n: Reserved for future result limiting.

    Returns:
        Final cluster records and mapping from temporary to final cluster ids.
    """
    _ = session_id, top_n
    total_reviews = len(reviews)
    all_sizes = [len(items) for items in grouped.values()]
    max_cluster_size = max(all_sizes) if all_sizes else 1

    clusters: list[ClusterRecord] = []
    temp_to_final_cluster: dict[str, str] = {}
    for group_index, (cluster_key, cluster_reviews) in enumerate(
        sorted(grouped.items(), key=lambda item: (-len(item[1]), item[0])),
        start=1,
    ):
        texts = [review.text_anonymized for review in cluster_reviews]
        concepts = [
            concept
            for review in cluster_reviews
            for concept in concepts_by_review.get(review.review_id, [])
        ]
        keywords = _extract_keywords(texts)
        concept_label = _concept_label(concepts)
        label = concept_label or (
            " / ".join(keywords[:2]) if keywords else f"cluster {group_index}"
        )
        sentiment = mean(
            sentiment_by_review[review.review_id] for review in cluster_reviews
        )
        trend_delta = _compute_trend_delta(cluster_reviews)
        trend_delta_norm = max(
            0.0, min(1.0, trend_delta / 3 if trend_delta > 0 else 0.0)
        )
        freq_norm = len(cluster_reviews) / max_cluster_size
        priority_score = round(
            (0.5 * freq_norm) + (0.3 * abs(sentiment)) + (0.2 * trend_delta_norm), 4
        )
        quote_ids = _select_top_quote_ids(cluster_reviews, sentiment_by_review)
        sources = sorted({review.source for review in cluster_reviews})
        confidence = (
            "low"
            if degraded_mode or cluster_key == "weak_signals"
            else _confidence_for_cluster(cluster_reviews)
        )
        degraded_reason = (
            "low_clustering_quality"
            if degraded_mode
            else ("weak_signals_mode" if cluster_key == "weak_signals" else None)
        )
        summary = _build_cluster_summary(
            label, cluster_reviews, total_reviews, sentiment, trend_delta, sources
        )
        final_cluster_id = f"{slugify(label)}_{group_index}"
        temp_to_final_cluster[cluster_key] = final_cluster_id
        clusters.append(
            ClusterRecord(
                cluster_id=final_cluster_id,
                label=label,
                summary=summary,
                review_ids=[review.review_id for review in cluster_reviews],
                top_quote_ids=quote_ids,
                priority_score=priority_score,
                sentiment_score=round(sentiment, 4),
                trend_delta=round(trend_delta, 4),
                confidence=confidence,
                degraded_reason=degraded_reason,
                keywords=keywords[:5],
                sources=sources,
                size=len(cluster_reviews),
            )
        )

    final_clusters = sorted(
        clusters,
        key=lambda cluster: (
            -cluster.priority_score,
            -cluster.size,
            cluster.cluster_id,
        ),
    )
    final_ids = {cluster.cluster_id for cluster in final_clusters}
    filtered_mapping = {
        temp_key: final_cluster_id
        for temp_key, final_cluster_id in temp_to_final_cluster.items()
        if final_cluster_id in final_ids
    }
    return final_clusters, filtered_mapping


def _extract_keywords(texts: list[str]) -> list[str]:
    """Extract representative keywords for a cluster.

    Args:
        texts: Cluster texts.

    Returns:
        Ranked keyword list.
    """
    if not texts:
        return []
    vectorizer = TfidfVectorizer(
        lowercase=True,
        analyzer="word",
        ngram_range=(1, 2),
        token_pattern=r"(?u)\b\w+\b",
        max_features=25,
        stop_words=sorted(STOPWORDS),
        max_df=0.9,
    )
    matrix = vectorizer.fit_transform(texts)
    scores = np.asarray(matrix.mean(axis=0)).ravel()
    if scores.size == 0:
        return []
    feature_names = np.asarray(vectorizer.get_feature_names_out())
    top_indices = scores.argsort()[::-1][:5]
    keywords = [feature_names[index] for index in top_indices if scores[index] > 0]
    return [
        keyword for keyword in keywords if keyword not in STOPWORDS and len(keyword) > 2
    ]


def _select_top_quote_ids(
    cluster_reviews: list[ReviewNormalized],
    sentiment_by_review: dict[str, float],
    limit: int = 3,
) -> list[str]:
    """Choose the most representative review ids for quoting.

    Args:
        cluster_reviews: Reviews assigned to a cluster.
        sentiment_by_review: Precomputed sentiment score by review id.
        limit: Maximum number of quote ids to return.

    Returns:
        Ranked review ids for quoting.
    """
    ranked = sorted(
        cluster_reviews,
        key=lambda review: (
            abs(sentiment_by_review[review.review_id]),
            len(review.text_anonymized),
        ),
        reverse=True,
    )
    return [review.review_id for review in ranked[:limit]]


def _build_cluster_summary(
    label: str,
    cluster_reviews: list[ReviewNormalized],
    total_reviews: int,
    sentiment: float,
    trend_delta: float,
    sources: list[str],
) -> str:
    """Render a short natural-language summary for one cluster.

    Args:
        label: Human-readable cluster label.
        cluster_reviews: Reviews assigned to the cluster.
        total_reviews: Total review count in the batch.
        sentiment: Mean sentiment score for the cluster.
        trend_delta: Relative week-over-week delta.
        sources: Distinct review sources represented in the cluster.

    Returns:
        Human-readable summary sentence.
    """
    share = (len(cluster_reviews) / max(1, total_reviews)) * 100
    mood = (
        "negative" if sentiment < -0.08 else "positive" if sentiment > 0.08 else "mixed"
    )
    trend_note = "spiking" if trend_delta > 0.5 else "stable"
    source_note = ", ".join(sources[:3])
    return (
        f"Theme '{label}' covers {len(cluster_reviews)} reviews ({share:.1f}% of the batch), "
        f"shows {mood} sentiment, and looks {trend_note}. Sources: {source_note}."
    )


def _compute_trend_delta(cluster_reviews: list[ReviewNormalized]) -> float:
    """Estimate latest-week trend change relative to prior history.

    Args:
        cluster_reviews: Reviews assigned to a cluster.

    Returns:
        Relative trend delta, where positive values imply growth.
    """
    weekly_counts: dict[str, int] = {}
    for review in cluster_reviews:
        week_key = review.created_at.astimezone(timezone.utc).strftime("%Y-W%W")
        weekly_counts[week_key] = weekly_counts.get(week_key, 0) + 1
    counts = [count for _, count in sorted(weekly_counts.items())]
    if len(counts) < 2:
        return 0.0
    baseline = mean(counts[:-1])
    latest = counts[-1]
    return round((latest - baseline) / max(1.0, baseline), 4)


def _confidence_for_cluster(cluster_reviews: list[ReviewNormalized]) -> str:
    """Assign a coarse confidence band based on cluster size.

    Args:
        cluster_reviews: Reviews assigned to the cluster.

    Returns:
        Confidence label.
    """
    size = len(cluster_reviews)
    if size >= 8:
        return "high"
    if size >= 4:
        return "medium"
    return "low"


def _build_alerts(
    clusters: list[ClusterRecord], reviews: list[ReviewNormalized]
) -> list[AlertRecord]:
    """Generate anomaly alerts from weekly cluster activity.

    Args:
        clusters: Ranked cluster records.
        reviews: Full review set for the batch.

    Returns:
        Informational and spike alerts derived from the batch history.
    """
    review_lookup = {review.review_id: review for review in reviews}
    alerts: list[AlertRecord] = []
    for cluster in clusters:
        dates = [
            review_lookup[review_id]
            .created_at.astimezone(timezone.utc)
            .strftime("%Y-W%W")
            for review_id in cluster.review_ids
            if review_id in review_lookup
        ]
        weekly_counts: dict[str, int] = {}
        for key in dates:
            weekly_counts[key] = weekly_counts.get(key, 0) + 1
        counts = [count for _, count in sorted(weekly_counts.items())]
        if len(counts) < 4:
            alerts.append(
                AlertRecord(
                    alert_id=f"alert_{cluster.cluster_id}_history",
                    cluster_id=cluster.cluster_id,
                    type="ANOMALY_CHECK",
                    severity="info",
                    reason="insufficient_history",
                    insufficient_history=True,
                    created_at=datetime.now(timezone.utc),
                )
            )
            continue
        baseline_counts = counts[:-1]
        latest = counts[-1]
        baseline_mean = mean(baseline_counts)
        baseline_std = pstdev(baseline_counts) if len(baseline_counts) > 1 else 0
        threshold = baseline_mean + (2 * baseline_std)
        if latest > threshold and latest > baseline_mean:
            spike_ratio = latest / max(1.0, baseline_mean)
            severity = "high" if spike_ratio >= 2 else "medium"
            alerts.append(
                AlertRecord(
                    alert_id=f"alert_{cluster.cluster_id}_spike",
                    cluster_id=cluster.cluster_id,
                    type="ANOMALY_SPIKE",
                    severity=severity,
                    reason=f"Latest week count {latest} exceeded baseline threshold {threshold:.2f}.",
                    spike_ratio=round(spike_ratio, 3),
                    created_at=datetime.now(timezone.utc),
                )
            )
    return alerts


def _quality_score(embeddings: np.ndarray, labels: np.ndarray) -> float:
    """Compute clustering quality while ignoring noise labels.

    Args:
        embeddings: Reduced embedding vectors.
        labels: Cluster labels aligned with the embeddings.

    Returns:
        Silhouette score or ``0.0`` when it cannot be computed.
    """
    usable_labels = labels[labels != -1]
    usable_embeddings = embeddings[labels != -1]
    if len(usable_labels) < 3 or len(set(usable_labels)) < 2:
        return 0.0
    try:
        return float(silhouette_score(usable_embeddings, usable_labels))
    except ValueError:
        return 0.0


def _agglomerative_profile(embeddings: np.ndarray) -> tuple[np.ndarray, float]:
    """Evaluate an agglomerative fallback clustering profile.

    Args:
        embeddings: Reduced embedding vectors.

    Returns:
        Tuple of ``(best_labels, best_quality_score)``.
    """
    n_samples = len(embeddings)
    best_quality = 0.0
    best_labels = np.zeros(n_samples, dtype=int)
    max_clusters = min(8, max(2, n_samples // 3))
    for n_clusters in range(3, max_clusters + 1):
        model = AgglomerativeClustering(
            n_clusters=n_clusters, metric="euclidean", linkage="ward"
        )
        labels = model.fit_predict(embeddings)
        quality = _quality_score(embeddings, labels)
        if quality > best_quality:
            best_quality = quality
            best_labels = labels
    return best_labels, best_quality


def detect_concepts(text: str) -> list[str]:
    """Detect predefined concept tags inside review text.

    Args:
        text: Review text.

    Returns:
        Matching concept identifiers.
    """
    normalized = normalize_text(text).lower()
    concepts: list[str] = []
    for concept, definition in CONCEPT_DEFINITIONS.items():
        for pattern in definition["patterns"]:
            if all(fragment in normalized for fragment in pattern):
                concepts.append(concept)
                break
    return concepts


def enriched_text(text: str, concepts: list[str]) -> str:
    """Boost clusterability by appending repeated concept markers.

    Args:
        text: Original review text.
        concepts: Detected concept identifiers.

    Returns:
        Text optionally enriched with synthetic concept tokens.
    """
    if not concepts:
        return text
    markers = " ".join(f"concept_{concept}" for concept in concepts for _ in range(3))
    return f"{text} {markers}"


def _concept_label(concepts: list[str]) -> str | None:
    """Convert dominant concept tags into a human-readable label.

    Args:
        concepts: Collected concept identifiers inside a cluster.

    Returns:
        Preferred label or ``None`` when no concept dominates.
    """
    if not concepts:
        return None
    counts: dict[str, int] = {}
    for concept in concepts:
        counts[concept] = counts.get(concept, 0) + 1
    dominant = max(counts.items(), key=lambda item: item[1])[0]
    return CONCEPT_DEFINITIONS[dominant]["label"]
