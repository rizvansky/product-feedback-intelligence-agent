from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from pfia.models import (
    AlertRecord,
    ClusterRecord,
    PreprocessingSummary,
    ReportArtifact,
    SessionRuntimeMetadata,
)
from pfia.utils import ensure_parent


def build_report_markdown(
    session_id: str,
    preprocessing_summary: PreprocessingSummary,
    clusters: list[ClusterRecord],
    alerts: list[AlertRecord],
    *,
    degraded_mode: bool,
    diagnostics: dict[str, object],
    executive_summary_override: str | None = None,
    runtime_metadata: SessionRuntimeMetadata | None = None,
) -> tuple[str, str]:
    """Build the Markdown report and executive summary for a session.

    Args:
        session_id: Session identifier shown in the report header.
        preprocessing_summary: Aggregate counters from preprocessing.
        clusters: Ranked clusters to include in the report body.
        alerts: Generated anomaly alerts.
        degraded_mode: Whether the run completed in degraded mode.
        diagnostics: Supplemental runtime diagnostics to surface in the report.
        executive_summary_override: Optional precomputed summary from an LLM agent.
        runtime_metadata: Optional runtime metadata snapshot for this session.

    Returns:
        Tuple of ``(markdown_report, executive_summary)``.
    """
    executive_summary = executive_summary_override or _build_executive_summary(
        clusters, alerts, degraded_mode
    )
    lines = [
        f"# PFIA Report for {session_id}",
        "",
        "## Executive Summary",
        "",
        executive_summary,
        "",
        "## Batch Overview",
        "",
        f"- Total records: {preprocessing_summary.total_records}",
        f"- Reviews kept after preprocessing: {preprocessing_summary.kept_records}",
        f"- Duplicate records removed: {preprocessing_summary.duplicate_records}",
        f"- Reviews quarantined by privacy gate: {preprocessing_summary.quarantined_records}",
        f"- Potential injection attempts: {preprocessing_summary.injection_hits}",
        f"- Low-information reviews: {preprocessing_summary.low_information_records}",
        f"- Degraded mode: {'yes' if degraded_mode else 'no'}",
        "",
        "## Top Themes",
        "",
        "| Cluster ID | Label | Reviews | Priority | Sentiment | Trend | Confidence |",
        "|---|---|---:|---:|---:|---:|---|",
    ]

    for cluster in clusters:
        lines.append(
            f"| `{cluster.cluster_id}` | {cluster.label} | {cluster.size} | "
            f"{cluster.priority_score:.2f} | {cluster.sentiment_score:.2f} | {cluster.trend_delta:.2f} | {cluster.confidence} |"
        )

    lines.extend(["", "## Theme Detail", ""])
    for cluster in clusters:
        lines.extend(
            [
                f"### {cluster.label} (`{cluster.cluster_id}`)",
                "",
                cluster.summary,
                "",
                f"- Keywords: {', '.join(cluster.keywords) if cluster.keywords else 'n/a'}",
                f"- Sources: {', '.join(cluster.sources) if cluster.sources else 'n/a'}",
                f"- Priority score: {cluster.priority_score:.2f}",
                f"- Confidence: {cluster.confidence}",
                f"- Degraded reason: {cluster.degraded_reason or 'n/a'}",
                "",
            ]
        )

    lines.extend(["## Alerts", ""])
    material_alerts = [alert for alert in alerts if not alert.insufficient_history]
    if material_alerts:
        for alert in material_alerts:
            lines.append(
                f"- `{alert.cluster_id}` [{alert.severity}] {alert.reason}"
                + (
                    f" Spike ratio: {alert.spike_ratio:.2f}."
                    if alert.spike_ratio is not None
                    else ""
                )
            )
    else:
        lines.append(
            "- No critical anomaly spikes were detected in the available history."
        )

    insufficient_history = [alert for alert in alerts if alert.insufficient_history]
    if insufficient_history:
        lines.extend(
            [
                "",
                "## Notes",
                "",
                "- Some clusters do not have enough weekly history for anomaly confirmation yet.",
            ]
        )

    lines.extend(
        [
            "",
            "## Run Diagnostics",
            "",
            f"- Clustering quality score: {diagnostics.get('quality_score', 0):.3f}",
            f"- Clustering backend: {diagnostics.get('clustering_backend_effective', 'n/a')}",
            f"- Selected clustering profile: {diagnostics.get('clustering_selected_profile', 'n/a')}",
            f"- Reflection attempts: {diagnostics.get('clustering_reflection_attempt_count', 0)}",
            f"- Total clusters included in report: {diagnostics.get('total_clusters', 0)}",
            f"- Degraded reason: {diagnostics.get('degraded_reason') or 'n/a'}",
            "",
        ]
    )
    if runtime_metadata is not None:
        lines.extend(_render_runtime_metadata(runtime_metadata))
    return "\n".join(lines).strip() + "\n", executive_summary


def write_report(
    report_path: Path,
    markdown: str,
    session_id: str,
    executive_summary: str,
    degraded_mode: bool,
) -> ReportArtifact:
    """Persist a Markdown report and return its metadata model.

    Args:
        report_path: Output file path.
        markdown: Rendered Markdown content.
        session_id: Owning session identifier.
        executive_summary: Short summary already generated for the report.
        degraded_mode: Whether the producing run completed in degraded mode.

    Returns:
        Report artifact metadata with embedded Markdown content.
    """
    ensure_parent(report_path)
    report_path.write_text(markdown, encoding="utf-8")
    generated_at = datetime.now(timezone.utc)
    return ReportArtifact(
        report_id=f"report_{session_id}",
        session_id=session_id,
        path=str(report_path),
        executive_summary=executive_summary,
        markdown=markdown,
        generated_at=generated_at,
        degraded_mode=degraded_mode,
    )


def _build_executive_summary(
    clusters: list[ClusterRecord], alerts: list[AlertRecord], degraded_mode: bool
) -> str:
    """Summarize the most important findings in one paragraph.

    Args:
        clusters: Ranked clusters included in the report.
        alerts: Generated anomaly alerts.
        degraded_mode: Whether the run completed in degraded mode.

    Returns:
        Human-readable executive summary text.
    """
    if not clusters:
        return "No themes were extracted from the uploaded batch."
    top = clusters[:3]
    themes = "; ".join(
        f"{cluster.label} ({cluster.size} reviews, priority {cluster.priority_score:.2f})"
        for cluster in top
    )
    alerts_count = len([alert for alert in alerts if not alert.insufficient_history])
    degraded_note = " The run completed in degraded mode." if degraded_mode else ""
    return (
        f"The batch is dominated by {themes}. "
        f"Detected anomaly spikes: {alerts_count}.{degraded_note}"
    )


def _render_runtime_metadata(runtime_metadata: SessionRuntimeMetadata) -> list[str]:
    """Render the runtime metadata appendix for the Markdown report.

    Args:
        runtime_metadata: Persisted metadata for the completed run.

    Returns:
        Markdown lines describing the effective runtime profile.
    """
    lines = [
        "## Runtime Metadata",
        "",
        f"- Runtime profile: `{runtime_metadata.runtime_profile}`",
        f"- Trace correlation id: `{runtime_metadata.trace_correlation_id}`",
        f"- Trace exporters effective: {', '.join(f'`{item}`' for item in runtime_metadata.trace_exporters_effective) or 'n/a'}",
        f"- Local trace path: `{runtime_metadata.trace_local_path or 'n/a'}`",
        f"- Requested orchestrator backend: `{runtime_metadata.orchestrator_backend_requested}`",
        f"- Effective orchestrator backend: `{runtime_metadata.orchestrator_backend_effective}`",
        f"- Requested generation backend: `{runtime_metadata.generation_backend_requested}`",
        f"- Effective generation backend: `{runtime_metadata.generation_backend_effective}`",
        f"- Requested retrieval backend: `{runtime_metadata.retrieval_backend_requested}`",
        f"- Effective retrieval backend: `{runtime_metadata.retrieval_backend_effective}`",
        f"- Requested PII backend: `{runtime_metadata.pii_backend_requested}`",
        f"- Effective PII backend: `{runtime_metadata.pii_backend_effective}`",
        f"- Requested sentiment backend: `{runtime_metadata.sentiment_backend_requested}`",
        f"- Effective sentiment backend: `{runtime_metadata.sentiment_backend_effective}`",
        f"- Effective sentiment model: `{runtime_metadata.sentiment_model_effective or 'n/a'}`",
        f"- Embedding backend: `{runtime_metadata.embedding_backend}`",
        f"- Requested embedding backend: `{runtime_metadata.embedding_backend_requested or runtime_metadata.embedding_backend}`",
        f"- Effective embedding backend: `{runtime_metadata.embedding_backend_effective or runtime_metadata.embedding_backend}`",
        f"- Effective embedding model: `{runtime_metadata.embedding_model_effective or 'n/a'}`",
        f"- OpenAI generation enabled: {'yes' if runtime_metadata.openai_generation_enabled else 'no'}",
        f"- Mistral fallback enabled: {'yes' if runtime_metadata.mistral_fallback_enabled else 'no'}",
        f"- Anthropic fallback enabled: {'yes' if runtime_metadata.anthropic_fallback_enabled else 'no'}",
        f"- Primary LLM model: `{runtime_metadata.llm_primary_model or 'n/a'}`",
        f"- LLM call count: `{runtime_metadata.llm_call_count}`",
        f"- Embedding call count: `{runtime_metadata.embedding_call_count}`",
        f"- Prompt tokens total: `{runtime_metadata.prompt_tokens_total}`",
        f"- Completion tokens total: `{runtime_metadata.completion_tokens_total}`",
        f"- Embedding input tokens total: `{runtime_metadata.embedding_input_tokens_total}`",
        f"- Estimated session cost USD: `{runtime_metadata.estimated_cost_usd:.6f}`",
        f"- Input filename: `{runtime_metadata.input_filename or 'n/a'}`",
        f"- Input content type: `{runtime_metadata.input_content_type or 'n/a'}`",
        f"- Records kept: `{runtime_metadata.records_kept}` of `{runtime_metadata.records_total}`",
        f"- Top cluster ids: {', '.join(f'`{cluster_id}`' for cluster_id in runtime_metadata.top_cluster_ids) or 'n/a'}",
        f"- Data dir: `{runtime_metadata.data_dir}`",
        f"- Chroma dir: `{runtime_metadata.chroma_persist_dir or 'n/a'}`",
        f"- Embedded worker: {'yes' if runtime_metadata.embedded_worker else 'no'}",
        "- Provider usage summary:",
    ]
    for provider, meta in runtime_metadata.provider_usage_summary.items():
        lines.append(
            "  - "
            f"`{provider}` -> llm_calls={meta.get('llm_calls', 0)}, "
            f"embedding_calls={meta.get('embedding_calls', 0)}, "
            f"models={', '.join(meta.get('models', [])) or 'n/a'}, "
            f"last_status={meta.get('last_status', 'unknown')}"
        )
    lines.extend(
        [
            "- Agent usage:",
        ]
    )
    for agent_name, meta in runtime_metadata.agent_usage.items():
        mode = str(meta.get("mode", "unknown"))
        used = "yes" if meta.get("used") else "no"
        model = str(meta.get("model") or "n/a")
        lines.append(f"  - `{agent_name}` -> used={used}, mode={mode}, model={model}")
    lines.extend([""])
    return lines
