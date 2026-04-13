from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from pfia.models import AlertRecord, ClusterRecord, PreprocessingSummary, ReportArtifact
from pfia.utils import ensure_parent


def build_report_markdown(
    session_id: str,
    preprocessing_summary: PreprocessingSummary,
    clusters: list[ClusterRecord],
    alerts: list[AlertRecord],
    *,
    degraded_mode: bool,
    diagnostics: dict[str, object],
) -> tuple[str, str]:
    executive_summary = _build_executive_summary(clusters, alerts, degraded_mode)
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
            f"- Total clusters included in report: {diagnostics.get('total_clusters', 0)}",
            f"- Degraded reason: {diagnostics.get('degraded_reason') or 'n/a'}",
            "",
        ]
    )
    return "\n".join(lines).strip() + "\n", executive_summary


def write_report(
    report_path: Path,
    markdown: str,
    session_id: str,
    executive_summary: str,
    degraded_mode: bool,
) -> ReportArtifact:
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
