from __future__ import annotations

import re
from pathlib import Path

from pfia.errors import PFIAError, SessionNotReadyError
from pfia.models import ChatAnswer, ToolName, ToolTrace
from pfia.retrieval import SessionRetriever


COMPARE_RE = re.compile(r"\b(compare|vs|versus|сравн)\b", re.IGNORECASE)
TREND_RE = re.compile(
    r"\b(trend|spike|growing|growth|динамик|всплеск|рост)\b", re.IGNORECASE
)
REPORT_RE = re.compile(r"\b(report|summary|executive|отч[её]т|резюме)\b", re.IGNORECASE)
PRIORITY_RE = re.compile(
    r"\b(highest|top|priority|urgent|critical|приоритет|главн|срочн)\b", re.IGNORECASE
)


def answer_question(index_path: Path, session_ready: bool, question: str) -> ChatAnswer:
    """Answer a grounded user question against a session retrieval index.

    Args:
        index_path: Path to the persisted retrieval index.
        session_ready: Whether the session finished processing successfully enough for Q&A.
        question: User question in free-form text.

    Returns:
        Fully grounded chat answer with evidence and tool trace.

    Raises:
        SessionNotReadyError: If the session is not yet ready for Q&A.
        PFIAError: If no evidence could be found for the question.
    """
    if not session_ready:
        raise SessionNotReadyError()
    retriever = SessionRetriever.load(index_path)
    tool_trace: list[ToolTrace] = []

    if PRIORITY_RE.search(question):
        hits = retriever.top_clusters(top_k=5)
        output_summary = f"Priority intent detected. Selected {len(hits)} clusters by priority score."
    else:
        hits = retriever.search_clusters(question, top_k=5)
        output_summary = f"Found {len(hits)} relevant clusters."
    tool_trace.append(
        ToolTrace(
            tool=ToolName.search_clusters,
            input={"query": question, "top_k": 5},
            output_summary=output_summary,
        )
    )
    if not hits:
        raise PFIAError(
            "NO_EVIDENCE_AVAILABLE",
            "No grounded evidence found for this question.",
            status_code=404,
        )
    top = hits[0]

    comparison_payload = None
    trend_notes = []
    report_snippet = ""

    if COMPARE_RE.search(question) and len(hits) >= 2:
        comparison_payload = retriever.compare_clusters(
            hits[0].cluster_id, hits[1].cluster_id
        )
        tool_trace.append(
            ToolTrace(
                tool=ToolName.compare_clusters,
                input={
                    "cluster_a": hits[0].cluster_id,
                    "cluster_b": hits[1].cluster_id,
                },
                output_summary="Prepared side-by-side comparison for the top two matching clusters.",
            )
        )

    if PRIORITY_RE.search(question):
        evidence = retriever.build_evidence(
            top.label,
            top_k=3,
            quote_limit=2,
            include_trends=bool(TREND_RE.search(question) or comparison_payload),
        )
    else:
        evidence = retriever.build_evidence(
            question,
            top_k=3,
            quote_limit=2,
            include_trends=bool(TREND_RE.search(question) or comparison_payload),
        )

    for hit in evidence.cluster_hits[:2]:
        tool_trace.append(
            ToolTrace(
                tool=ToolName.get_quotes,
                input={"cluster_id": hit.cluster_id, "limit": 2},
                output_summary=f"Loaded supporting quotes for {hit.cluster_id}.",
            )
        )

    if TREND_RE.search(question) or comparison_payload:
        for trend in evidence.trends:
            tool_trace.append(
                ToolTrace(
                    tool=ToolName.get_trend,
                    input={"cluster_id": trend.cluster_id},
                    output_summary=f"Trend for {trend.cluster_id}: {trend.note} ({trend.trend_delta:+.2f}).",
                )
            )
            trend_notes.append(
                f"`{trend.cluster_id}` is {trend.note} ({trend.trend_delta:+.2f})."
            )

    if REPORT_RE.search(question):
        report_snippet = retriever.get_report_section("executive_summary")
        tool_trace.append(
            ToolTrace(
                tool=ToolName.get_report_section,
                input={"section_name": "executive_summary"},
                output_summary="Loaded executive summary section from the report.",
            )
        )

    answer = _compose_answer(
        question, evidence, comparison_payload, trend_notes, report_snippet
    )
    return ChatAnswer(
        answer=answer, evidence=evidence, tool_trace=tool_trace, degraded_mode=False
    )


def _compose_answer(
    question: str,
    evidence,
    comparison_payload,
    trend_notes: list[str],
    report_snippet: str,
) -> str:
    """Compose the final natural-language answer from grounded evidence.

    Args:
        question: Original user question.
        evidence: Retrieved evidence bundle.
        comparison_payload: Optional comparison data for two clusters.
        trend_notes: Optional trend remarks to append.
        report_snippet: Optional report section excerpt.

    Returns:
        Rendered answer text.
    """
    top = evidence.cluster_hits[0]
    lines = [
        f"The strongest grounded match for this question is `{top.cluster_id}` ({top.label}). {top.summary}",
    ]

    if comparison_payload:
        left = comparison_payload["cluster_a"]
        right = comparison_payload["cluster_b"]
        lines.append(
            "Comparison: "
            f"`{left['cluster_id']}` has priority {left['priority_score']:.2f} and sentiment {left['sentiment_score']:.2f}, "
            f"while `{right['cluster_id']}` has priority {right['priority_score']:.2f} and sentiment {right['sentiment_score']:.2f}."
        )

    if trend_notes:
        lines.append("Trend view: " + " ".join(trend_notes))

    if report_snippet:
        lines.append("Report context: " + report_snippet)

    if evidence.quotes:
        quote_lines = []
        for quote in evidence.quotes[:3]:
            quote_lines.append(f"`{quote.cluster_id}`: “{quote.text}”")
        lines.append("Evidence: " + " | ".join(quote_lines))

    lines.append(
        "Referenced clusters: "
        + ", ".join(f"`{hit.cluster_id}`" for hit in evidence.cluster_hits[:3])
    )
    return "\n\n".join(lines)
