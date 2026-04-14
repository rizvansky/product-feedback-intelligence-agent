from __future__ import annotations

import json
from typing import Any

from pfia.config import Settings
from pfia.models import (
    AlertRecord,
    ClusterRecord,
    PreprocessingSummary,
    ReviewNormalized,
)
from pfia.openai_client import (
    AnthropicClient,
    FallbackRoutingClient,
    MistralClient,
    OpenAIClient,
)
from pfia.utils import normalize_text, tokenize


def llm_generation_enabled(settings: Settings) -> bool:
    """Return whether external LLM-backed generation should be used.

    Args:
        settings: Runtime configuration.

    Returns:
        ``True`` when generation backend is ``openai`` and at least one provider is configured.
    """
    return settings.generation_backend == "openai" and (
        bool(settings.openai_api_key.strip())
        or bool(settings.mistral_api_key.strip())
        or bool(settings.anthropic_api_key.strip())
    )


def build_generation_client(
    settings: Settings,
    *,
    http_client=None,
    model: str | None = None,
) -> FallbackRoutingClient:
    """Construct the shared routed LLM client for runtime agent calls.

    Args:
        settings: Runtime configuration.
        http_client: Optional injected HTTP client, mainly for tests.
        model: Optional explicit model override.

    Returns:
        Configured provider-routing client instance.
    """
    primary = OpenAIClient(
        api_key=settings.openai_api_key,
        base_url=settings.openai_base_url,
        default_model=model or settings.llm_primary_model,
        timeout_s=settings.openai_timeout_s,
        max_retries=settings.openai_max_retries,
        http_client=http_client,
    )
    fallback = MistralClient(
        api_key=settings.mistral_api_key,
        base_url=settings.mistral_base_url,
        default_model=settings.llm_fallback_model,
        timeout_s=settings.openai_timeout_s,
        max_retries=settings.openai_max_retries,
        http_client=http_client,
    )
    secondary_fallback = AnthropicClient(
        api_key=settings.anthropic_api_key,
        base_url=settings.anthropic_base_url,
        default_model=settings.llm_second_fallback_model,
        timeout_s=settings.openai_timeout_s,
        max_retries=settings.openai_max_retries,
        http_client=http_client,
        api_version=settings.anthropic_api_version,
    )
    return FallbackRoutingClient(
        primary=primary if primary.available else None,
        fallbacks=[
            client for client in (fallback, secondary_fallback) if client.available
        ],
    )


def build_openai_client(
    settings: Settings,
    *,
    http_client=None,
    model: str | None = None,
) -> FallbackRoutingClient:
    """Backward-compatible alias for the routed generation client builder."""

    return build_generation_client(
        settings,
        http_client=http_client,
        model=model,
    )


def _client_mode(client: Any) -> str:
    """Return the effective provider mode used by a routed LLM client."""

    provider = getattr(client, "last_provider_used", None)
    if isinstance(provider, str) and provider:
        return provider
    if isinstance(getattr(client, "provider_name", None), str):
        return getattr(client, "provider_name")
    return "openai"


def _client_model(client: Any) -> str:
    """Return the effective model name used by a routed LLM client."""

    model = getattr(client, "last_model_used", None)
    if isinstance(model, str) and model:
        return model
    default_model = getattr(client, "default_model", "")
    return default_model if isinstance(default_model, str) else ""


def refine_clusters_with_llm(
    clusters: list[ClusterRecord],
    reviews: list[ReviewNormalized],
    settings: Settings,
    *,
    client: OpenAIClient | None = None,
) -> tuple[list[ClusterRecord], dict[str, Any]]:
    """Refine cluster labels and summaries with an LLM taxonomy agent.

    Args:
        clusters: Existing cluster records produced by deterministic analysis.
        reviews: Sanitized reviews from the session.
        settings: Runtime configuration.
        client: Optional prebuilt OpenAI client.

    Returns:
        Tuple of updated clusters and metadata about the agent run.
    """
    if not llm_generation_enabled(settings) or not clusters:
        return clusters, {"used": False, "mode": "local"}

    review_by_id = {review.review_id: review for review in reviews}
    payload = {
        "clusters": [
            {
                "cluster_id": cluster.cluster_id,
                "local_label": cluster.label,
                "local_summary": cluster.summary,
                "keywords": cluster.keywords,
                "sources": cluster.sources,
                "size": cluster.size,
                "sentiment_score": cluster.sentiment_score,
                "trend_delta": cluster.trend_delta,
                "sample_reviews": [
                    review_by_id[review_id].text_anonymized
                    for review_id in cluster.review_ids[:5]
                    if review_id in review_by_id
                ],
            }
            for cluster in clusters
        ]
    }

    messages = [
        {
            "role": "system",
            "content": (
                "You are TaxonomyAgent for PFIA. "
                "You receive anonymized review clusters and must improve human-readable labels and summaries. "
                "Return strict JSON with a top-level 'clusters' array. "
                "For each item include: cluster_id, label, summary, confidence. "
                "Rules: keep labels short, concrete, and product-manager-friendly; "
                "summaries must be grounded only in the provided examples and stats; "
                "confidence must be one of high, medium, low; "
                "do not mention data that is not present."
            ),
        },
        {
            "role": "user",
            "content": (
                "Refine these PFIA cluster drafts. "
                "Keep the same cluster_id values.\n\n"
                f"{json.dumps(payload, ensure_ascii=False)}"
            ),
        },
    ]

    agent_client = client or build_openai_client(settings)
    try:
        result = agent_client.complete_json(
            messages,
            max_tokens=1600,
            temperature=0.1,
        )
    except Exception as exc:  # pragma: no cover - fallback path exercised elsewhere
        return clusters, {"used": False, "mode": "fallback", "reason": str(exc)}

    updates = {
        str(item.get("cluster_id")): item
        for item in result.get("clusters", [])
        if item.get("cluster_id")
    }
    refined: list[ClusterRecord] = []
    changed = 0
    for cluster in clusters:
        update = updates.get(cluster.cluster_id)
        if not update:
            refined.append(cluster)
            continue
        label = _clean_label(str(update.get("label") or cluster.label))
        summary = _clean_summary(str(update.get("summary") or cluster.summary))
        confidence = _clean_confidence(
            str(update.get("confidence") or cluster.confidence)
        )
        if (
            label != cluster.label
            or summary != cluster.summary
            or confidence != cluster.confidence
        ):
            changed += 1
        refined.append(
            cluster.model_copy(
                update={
                    "label": label,
                    "summary": summary,
                    "confidence": confidence,
                }
            )
        )

    return refined, {
        "used": True,
        "mode": _client_mode(agent_client),
        "model": _client_model(agent_client),
        "changed_clusters": changed,
    }


def generate_executive_summary_with_llm(
    session_id: str,
    preprocessing_summary: PreprocessingSummary,
    clusters: list[ClusterRecord],
    alerts: list[AlertRecord],
    *,
    degraded_mode: bool,
    diagnostics: dict[str, object],
    settings: Settings,
    client: OpenAIClient | None = None,
) -> tuple[str | None, dict[str, Any]]:
    """Generate an executive summary with a dedicated report agent.

    Args:
        session_id: Session identifier.
        preprocessing_summary: Aggregate preprocessing counters.
        clusters: Ranked cluster records.
        alerts: Alert records attached to the session.
        degraded_mode: Whether the job completed in degraded mode.
        diagnostics: Supplemental runtime diagnostics.
        settings: Runtime configuration.
        client: Optional prebuilt OpenAI client.

    Returns:
        Tuple of optional summary text and metadata about the agent run.
    """
    if not llm_generation_enabled(settings) or not clusters:
        return None, {"used": False, "mode": "local"}

    alert_payload = [
        {
            "cluster_id": alert.cluster_id,
            "severity": alert.severity,
            "reason": alert.reason,
            "insufficient_history": alert.insufficient_history,
        }
        for alert in alerts
    ]
    cluster_payload = [
        {
            "cluster_id": cluster.cluster_id,
            "label": cluster.label,
            "summary": cluster.summary,
            "priority_score": cluster.priority_score,
            "size": cluster.size,
            "sentiment_score": cluster.sentiment_score,
            "trend_delta": cluster.trend_delta,
            "confidence": cluster.confidence,
        }
        for cluster in clusters[:5]
    ]
    messages = [
        {
            "role": "system",
            "content": (
                "You are ExecutiveSummaryAgent for PFIA. "
                "Write one grounded executive summary for a product manager. "
                "Return strict JSON with key 'executive_summary'. "
                "The summary must be concise, factual, and only use the provided data. "
                "Mention the top themes, alert posture, and degraded mode when relevant."
            ),
        },
        {
            "role": "user",
            "content": (
                f"Session: {session_id}\n"
                f"Preprocessing summary: {json.dumps(preprocessing_summary.model_dump(mode='json'), ensure_ascii=False)}\n"
                f"Top clusters: {json.dumps(cluster_payload, ensure_ascii=False)}\n"
                f"Alerts: {json.dumps(alert_payload, ensure_ascii=False)}\n"
                f"Diagnostics: {json.dumps(diagnostics, ensure_ascii=False)}\n"
                f"Degraded mode: {degraded_mode}"
            ),
        },
    ]

    agent_client = client or build_openai_client(settings)
    try:
        result = agent_client.complete_json(
            messages,
            max_tokens=500,
            temperature=0.1,
        )
    except Exception as exc:  # pragma: no cover - fallback path exercised elsewhere
        return None, {"used": False, "mode": "fallback", "reason": str(exc)}

    summary = _clean_summary(str(result.get("executive_summary", "")))
    if not summary:
        return None, {"used": False, "mode": "fallback", "reason": "empty summary"}
    return summary, {
        "used": True,
        "mode": _client_mode(agent_client),
        "model": _client_model(agent_client),
    }


def review_preprocessing_flags_with_llm(
    reviews: list[ReviewNormalized],
    settings: Settings,
    *,
    client: OpenAIClient | None = None,
) -> tuple[list[ReviewNormalized], dict[str, Any]]:
    """Run an LLM second pass over borderline preprocessing flags.

    Args:
        reviews: Sanitized reviews with heuristic flags already attached.
        settings: Runtime configuration.
        client: Optional prebuilt OpenAI client.

    Returns:
        Tuple of updated reviews and metadata about the classifier run.
    """
    if not llm_generation_enabled(settings) or not reviews:
        return reviews, {"used": False, "mode": "local", "candidates": 0}

    candidates = []
    for review in reviews:
        flags = [
            flag
            for flag in review.flags
            if flag in {"spam", "low_information", "injection_suspected"}
        ]
        if not flags:
            continue
        candidates.append(
            {
                "review_id": review.review_id,
                "text": review.text_anonymized,
                "flags": flags,
                "source": review.source,
                "language": review.language,
            }
        )

    if not candidates:
        return reviews, {"used": False, "mode": "local", "candidates": 0}

    messages = [
        {
            "role": "system",
            "content": (
                "You are PreprocessReviewAgent for PFIA. "
                "You receive anonymized reviews that were heuristically flagged as spam, prompt injection, "
                "or low-information. Return strict JSON with a top-level 'reviews' array. "
                "For each item include: review_id, keep_spam, keep_injection, keep_low_information, note. "
                "Keep flags only when the evidence in the review text supports them. "
                "Do not invent categories outside the provided flags."
            ),
        },
        {
            "role": "user",
            "content": json.dumps({"reviews": candidates}, ensure_ascii=False),
        },
    ]

    agent_client = client or build_openai_client(settings)
    try:
        result = agent_client.complete_json(
            messages,
            max_tokens=1400,
            temperature=0.1,
        )
    except Exception as exc:  # pragma: no cover - fallback path exercised elsewhere
        return reviews, {"used": False, "mode": "fallback", "reason": str(exc)}

    updates = {
        str(item.get("review_id")): item
        for item in result.get("reviews", [])
        if item.get("review_id")
    }
    updated_reviews: list[ReviewNormalized] = []
    reviewed = 0
    removed_flags = 0
    for review in reviews:
        update = updates.get(review.review_id)
        if not update:
            updated_reviews.append(review)
            continue
        reviewed += 1
        flags = list(review.flags)
        next_flags = []
        for flag in flags:
            if flag == "spam":
                if _as_bool(update.get("keep_spam"), True):
                    next_flags.append(flag)
                else:
                    removed_flags += 1
            elif flag == "injection_suspected":
                if _as_bool(update.get("keep_injection"), True):
                    next_flags.append(flag)
                else:
                    removed_flags += 1
            elif flag == "low_information":
                if _as_bool(update.get("keep_low_information"), True):
                    next_flags.append(flag)
                else:
                    removed_flags += 1
            else:
                next_flags.append(flag)
        metadata = dict(review.metadata)
        note = _clean_summary(str(update.get("note") or ""))
        if note:
            metadata["preprocess_review_note"] = note
        updated_reviews.append(
            review.model_copy(
                update={
                    "flags": next_flags,
                    "metadata": metadata,
                }
            )
        )

    return updated_reviews, {
        "used": True,
        "mode": _client_mode(agent_client),
        "model": _client_model(agent_client),
        "candidates": len(candidates),
        "reviewed": reviewed,
        "removed_flags": removed_flags,
    }


def review_clusters_with_llm(
    clusters: list[ClusterRecord],
    reviews: list[ReviewNormalized],
    cluster_by_review: dict[str, str],
    settings: Settings,
    *,
    client: OpenAIClient | None = None,
) -> tuple[list[ClusterRecord], dict[str, str], dict[str, Any]]:
    """Review cluster quality and apply safe LLM-guided merge/split decisions.

    Args:
        clusters: Deterministic cluster records.
        reviews: Sanitized review set for the session.
        cluster_by_review: Current mapping from review ids to cluster ids.
        settings: Runtime configuration.
        client: Optional prebuilt OpenAI client.

    Returns:
        Tuple of updated clusters, updated review-to-cluster mapping, and metadata.
    """
    if not llm_generation_enabled(settings) or len(clusters) < 2:
        return clusters, cluster_by_review, {"used": False, "mode": "local"}

    review_by_id = {review.review_id: review for review in reviews}
    payload = {
        "clusters": [
            {
                "cluster_id": cluster.cluster_id,
                "label": cluster.label,
                "summary": cluster.summary,
                "keywords": cluster.keywords,
                "size": cluster.size,
                "priority_score": cluster.priority_score,
                "sentiment_score": cluster.sentiment_score,
                "trend_delta": cluster.trend_delta,
                "sample_reviews": [
                    review_by_id[review_id].text_anonymized
                    for review_id in cluster.review_ids[:5]
                    if review_id in review_by_id
                ],
            }
            for cluster in clusters[:10]
        ]
    }
    messages = [
        {
            "role": "system",
            "content": (
                "You are ClusterReviewAgent for PFIA. "
                "Review existing anonymized clusters and decide if any pair should be merged "
                "because they describe the same user problem, or if any cluster looks too broad "
                "and should be flagged for split review. "
                "Return strict JSON with keys merge_pairs, split_clusters, notes. "
                "merge_pairs items must have left_cluster_id, right_cluster_id, reason. "
                "split_clusters items must have cluster_id, reason. "
                "Be conservative: prefer zero decisions over weak guesses."
            ),
        },
        {
            "role": "user",
            "content": json.dumps(payload, ensure_ascii=False),
        },
    ]

    agent_client = client or build_openai_client(settings)
    try:
        result = agent_client.complete_json(
            messages,
            max_tokens=1200,
            temperature=0.1,
        )
    except Exception as exc:  # pragma: no cover - fallback path exercised elsewhere
        return (
            clusters,
            cluster_by_review,
            {"used": False, "mode": "fallback", "reason": str(exc)},
        )

    merge_pairs = [
        pair
        for pair in result.get("merge_pairs", [])
        if isinstance(pair, dict)
        and pair.get("left_cluster_id")
        and pair.get("right_cluster_id")
    ]
    split_clusters = [
        item
        for item in result.get("split_clusters", [])
        if isinstance(item, dict) and item.get("cluster_id")
    ]
    updated_clusters, updated_mapping, merge_count = _apply_merge_pairs(
        clusters,
        cluster_by_review,
        merge_pairs,
    )
    split_count = 0
    if split_clusters:
        split_by_id = {
            str(item["cluster_id"]): _clean_summary(str(item.get("reason") or ""))
            for item in split_clusters
        }
        adjusted_clusters: list[ClusterRecord] = []
        for cluster in updated_clusters:
            reason = split_by_id.get(cluster.cluster_id)
            if not reason:
                adjusted_clusters.append(cluster)
                continue
            split_count += 1
            degraded_reason = "llm_split_review"
            if reason:
                degraded_reason = f"llm_split_review: {reason}"
            adjusted_clusters.append(
                cluster.model_copy(
                    update={
                        "confidence": "low",
                        "degraded_reason": degraded_reason[:160],
                    }
                )
            )
        updated_clusters = adjusted_clusters

    updated_clusters = sorted(
        updated_clusters,
        key=lambda cluster: (
            -cluster.priority_score,
            -cluster.size,
            cluster.cluster_id,
        ),
    )
    return (
        updated_clusters,
        updated_mapping,
        {
            "used": True,
            "mode": _client_mode(agent_client),
            "model": _client_model(agent_client),
            "merge_recommendations": len(merge_pairs),
            "split_recommendations": len(split_clusters),
            "applied_merges": merge_count,
            "applied_split_marks": split_count,
            "notes": result.get("notes", []),
        },
    )


def explain_alerts_with_llm(
    alerts: list[AlertRecord],
    clusters: list[ClusterRecord],
    settings: Settings,
    *,
    client: OpenAIClient | None = None,
) -> tuple[list[AlertRecord], dict[str, Any]]:
    """Rewrite anomaly alert reasons into grounded PM-friendly explanations.

    Args:
        alerts: Deterministic alert records.
        clusters: Cluster records referenced by the alerts.
        settings: Runtime configuration.
        client: Optional prebuilt OpenAI client.

    Returns:
        Tuple of updated alerts and metadata about the explainer run.
    """
    material_alerts = [alert for alert in alerts if not alert.insufficient_history]
    if not llm_generation_enabled(settings) or not material_alerts:
        return alerts, {"used": False, "mode": "local", "alerts": len(material_alerts)}

    cluster_by_id = {cluster.cluster_id: cluster for cluster in clusters}
    payload = {
        "alerts": [
            {
                "alert_id": alert.alert_id,
                "cluster_id": alert.cluster_id,
                "current_reason": alert.reason,
                "severity": alert.severity,
                "spike_ratio": alert.spike_ratio,
                "cluster": {
                    "label": cluster_by_id.get(alert.cluster_id).label
                    if cluster_by_id.get(alert.cluster_id)
                    else alert.cluster_id,
                    "summary": cluster_by_id.get(alert.cluster_id).summary
                    if cluster_by_id.get(alert.cluster_id)
                    else "",
                    "size": cluster_by_id.get(alert.cluster_id).size
                    if cluster_by_id.get(alert.cluster_id)
                    else None,
                    "priority_score": cluster_by_id.get(alert.cluster_id).priority_score
                    if cluster_by_id.get(alert.cluster_id)
                    else None,
                    "trend_delta": cluster_by_id.get(alert.cluster_id).trend_delta
                    if cluster_by_id.get(alert.cluster_id)
                    else None,
                },
            }
            for alert in material_alerts
        ]
    }
    messages = [
        {
            "role": "system",
            "content": (
                "You are AnomalyExplainerAgent for PFIA. "
                "Rewrite anomaly reasons for a product manager. "
                "Return strict JSON with a top-level 'alerts' array. "
                "Each item must contain alert_id and explanation. "
                "The explanation must stay grounded in the provided cluster and alert stats."
            ),
        },
        {
            "role": "user",
            "content": json.dumps(payload, ensure_ascii=False),
        },
    ]

    agent_client = client or build_openai_client(settings)
    try:
        result = agent_client.complete_json(
            messages,
            max_tokens=1000,
            temperature=0.1,
        )
    except Exception as exc:  # pragma: no cover - fallback path exercised elsewhere
        return alerts, {"used": False, "mode": "fallback", "reason": str(exc)}

    explanations = {
        str(item.get("alert_id")): _clean_summary(str(item.get("explanation") or ""))
        for item in result.get("alerts", [])
        if item.get("alert_id")
    }
    updated = []
    changed = 0
    for alert in alerts:
        explanation = explanations.get(alert.alert_id)
        if explanation and not alert.insufficient_history:
            changed += 1
            updated.append(alert.model_copy(update={"reason": explanation}))
        else:
            updated.append(alert)
    return updated, {
        "used": True,
        "mode": _client_mode(agent_client),
        "model": _client_model(agent_client),
        "changed_alerts": changed,
    }


def _clean_label(value: str) -> str:
    """Normalize a cluster label returned by the taxonomy agent."""
    cleaned = normalize_text(value).strip(" .")
    if not cleaned:
        return "Untitled cluster"
    return cleaned[:80]


def _clean_summary(value: str) -> str:
    """Normalize a summary returned by an LLM report agent."""
    cleaned = normalize_text(value).strip()
    return cleaned[:400]


def _clean_confidence(value: str) -> str:
    """Normalize an LLM confidence label into the supported bands."""
    normalized = normalize_text(value).lower()
    if normalized in {"high", "medium", "low"}:
        return normalized
    return "medium"


def _as_bool(value: Any, default: bool) -> bool:
    """Coerce loose JSON-like boolean values into ``bool``."""
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        lowered = normalize_text(value).lower()
        if lowered in {"true", "yes", "1"}:
            return True
        if lowered in {"false", "no", "0"}:
            return False
    return default


def _apply_merge_pairs(
    clusters: list[ClusterRecord],
    cluster_by_review: dict[str, str],
    merge_pairs: list[dict[str, Any]],
) -> tuple[list[ClusterRecord], dict[str, str], int]:
    """Apply safe cluster merges suggested by the review agent."""
    cluster_map = {cluster.cluster_id: cluster for cluster in clusters}
    consumed: set[str] = set()
    merged_clusters: list[ClusterRecord] = []
    updated_mapping = dict(cluster_by_review)
    applied = 0

    for pair in merge_pairs:
        left_id = str(pair["left_cluster_id"])
        right_id = str(pair["right_cluster_id"])
        if left_id == right_id or left_id in consumed or right_id in consumed:
            continue
        left = cluster_map.get(left_id)
        right = cluster_map.get(right_id)
        if left is None or right is None:
            continue
        if not _merge_guard(left, right):
            continue
        primary, secondary = _select_primary_cluster(left, right)
        merged = _merge_two_clusters(primary, secondary)
        merged_clusters.append(merged)
        consumed.add(primary.cluster_id)
        consumed.add(secondary.cluster_id)
        for review_id, cluster_id in updated_mapping.items():
            if cluster_id == secondary.cluster_id:
                updated_mapping[review_id] = primary.cluster_id
        applied += 1

    for cluster in clusters:
        if cluster.cluster_id not in consumed:
            merged_clusters.append(cluster)

    return merged_clusters, updated_mapping, applied


def _merge_guard(left: ClusterRecord, right: ClusterRecord) -> bool:
    """Return whether a merge recommendation passes a deterministic safety guard."""
    left_tokens = _cluster_token_set(left)
    right_tokens = _cluster_token_set(right)
    if not left_tokens or not right_tokens:
        return False
    overlap = len(left_tokens & right_tokens) / max(1, len(left_tokens | right_tokens))
    sentiment_gap = abs(left.sentiment_score - right.sentiment_score)
    return overlap >= 0.1 and sentiment_gap <= 0.6


def _cluster_token_set(cluster: ClusterRecord) -> set[str]:
    """Build a lexical token set for merge-guard comparisons."""
    return {
        token
        for token in tokenize(
            " ".join(
                [cluster.label, cluster.summary, *cluster.keywords, *cluster.sources]
            )
        )
        if len(token) > 2
    }


def _select_primary_cluster(
    left: ClusterRecord, right: ClusterRecord
) -> tuple[ClusterRecord, ClusterRecord]:
    """Pick the primary cluster that keeps the surviving cluster id."""
    if (left.size, left.priority_score, left.cluster_id) >= (
        right.size,
        right.priority_score,
        right.cluster_id,
    ):
        return left, right
    return right, left


def _merge_two_clusters(
    primary: ClusterRecord, secondary: ClusterRecord
) -> ClusterRecord:
    """Merge two compatible clusters into one surviving record."""
    total_size = primary.size + secondary.size
    sentiment = (
        (primary.sentiment_score * primary.size)
        + (secondary.sentiment_score * secondary.size)
    ) / max(1, total_size)
    trend_delta = (
        (primary.trend_delta * primary.size) + (secondary.trend_delta * secondary.size)
    ) / max(1, total_size)
    priority_score = (
        (primary.priority_score * primary.size)
        + (secondary.priority_score * secondary.size)
    ) / max(1, total_size)
    top_quote_ids = list(
        dict.fromkeys(primary.top_quote_ids + secondary.top_quote_ids)
    )[:5]
    keywords = list(dict.fromkeys(primary.keywords + secondary.keywords))[:5]
    sources = sorted({*primary.sources, *secondary.sources})
    confidence_rank = {"low": 0, "medium": 1, "high": 2}
    merged_confidence = (
        primary.confidence
        if confidence_rank.get(primary.confidence, 0)
        <= confidence_rank.get(secondary.confidence, 0)
        else secondary.confidence
    )
    merged_reason = "llm_merge_review"
    if primary.degraded_reason or secondary.degraded_reason:
        merged_reason = "; ".join(
            reason
            for reason in [
                primary.degraded_reason,
                secondary.degraded_reason,
                "llm_merge_review",
            ]
            if reason
        )[:160]
    return primary.model_copy(
        update={
            "summary": primary.summary,
            "review_ids": list(
                dict.fromkeys(primary.review_ids + secondary.review_ids)
            ),
            "top_quote_ids": top_quote_ids,
            "priority_score": round(priority_score, 4),
            "sentiment_score": round(sentiment, 4),
            "trend_delta": round(trend_delta, 4),
            "confidence": merged_confidence,
            "degraded_reason": merged_reason,
            "keywords": keywords,
            "sources": sources,
            "size": total_size,
            "anomaly_flag": primary.anomaly_flag or secondary.anomaly_flag,
        }
    )
