from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from pfia.config import Settings
from pfia.errors import PFIAError, SessionNotReadyError
from pfia.llm_agents import build_openai_client, llm_generation_enabled
from pfia.models import (
    ChatAnswer,
    ClusterHit,
    EvidenceBundle,
    QuoteRecord,
    ToolName,
    ToolTrace,
    TrendSnippet,
)
from pfia.retrieval import SessionRetriever


COMPARE_RE = re.compile(r"\b(compare|vs|versus|сравн)\b", re.IGNORECASE)
TREND_RE = re.compile(
    r"\b(trend|spike|growing|growth|динамик|всплеск|рост)\b", re.IGNORECASE
)
REPORT_RE = re.compile(r"\b(report|summary|executive|отч[её]т|резюме)\b", re.IGNORECASE)
PRIORITY_RE = re.compile(
    r"\b(highest|top|priority|urgent|critical|приоритет|главн|срочн)\b", re.IGNORECASE
)


def answer_question(
    index_path: Path,
    session_ready: bool,
    question: str,
    *,
    settings: Settings | None = None,
    chat_history: list[dict[str, str]] | None = None,
) -> ChatAnswer:
    """Answer a grounded user question against a session retrieval index.

    Args:
        index_path: Path to the persisted retrieval index.
        session_ready: Whether the session finished processing successfully enough for Q&A.
        question: User question in free-form text.
        settings: Optional runtime settings used to enable LLM agents.
        chat_history: Optional recent chat turns for context.

    Returns:
        Fully grounded chat answer with evidence and tool trace.

    Raises:
        SessionNotReadyError: If the session is not yet ready for Q&A.
        PFIAError: If no evidence could be found for the question.
    """
    if not session_ready:
        raise SessionNotReadyError()

    if settings is not None and llm_generation_enabled(settings):
        try:
            return _answer_question_with_llm(
                index_path,
                question,
                settings=settings,
                chat_history=chat_history or [],
            )
        except PFIAError as exc:
            if exc.code == "NO_EVIDENCE_AVAILABLE":
                raise
            fallback = _answer_question_local(index_path, question, settings=settings)
            return fallback.model_copy(update={"degraded_mode": True})
        except Exception:  # pragma: no cover - defensive fallback
            fallback = _answer_question_local(index_path, question, settings=settings)
            return fallback.model_copy(update={"degraded_mode": True})

    return _answer_question_local(index_path, question, settings=settings)


def _answer_question_local(
    index_path: Path, question: str, *, settings: Settings | None = None
) -> ChatAnswer:
    """Answer a question using the deterministic local retriever path.

    Args:
        index_path: Path to the persisted retrieval index.
        question: User question.

    Returns:
        Grounded answer produced without LLM orchestration.
    """
    retriever = SessionRetriever.load(index_path, settings=settings)
    tool_trace: list[ToolTrace] = []

    if PRIORITY_RE.search(question):
        hits = retriever.top_clusters(top_k=5)
        output_summary = f"Priority intent detected. Selected {len(hits)} clusters by priority score."
        tool_trace.append(
            ToolTrace(
                tool=ToolName.top_clusters,
                input={"top_k": 5},
                output_summary=output_summary,
            )
        )
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

    comparison_payload = None
    trend_notes: list[str] = []
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

    evidence_query = hits[0].label if PRIORITY_RE.search(question) else question
    evidence = retriever.build_evidence(
        evidence_query,
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
        question,
        evidence,
        comparison_payload=comparison_payload,
        trend_notes=trend_notes,
        report_snippet=report_snippet,
    )
    return ChatAnswer(
        answer=answer, evidence=evidence, tool_trace=tool_trace, degraded_mode=False
    )


def _answer_question_with_llm(
    index_path: Path,
    question: str,
    *,
    settings: Settings,
    chat_history: list[dict[str, str]],
) -> ChatAnswer:
    """Answer a question with an external LLM planner and answer-writer agent.

    Args:
        index_path: Path to the persisted retrieval index.
        question: User question.
        settings: Runtime settings with OpenAI configuration.
        chat_history: Recent chat turns to include in the planner context.

    Returns:
        Grounded answer produced by the external LLM agent path.
    """
    retriever = SessionRetriever.load(index_path, settings=settings)
    client = build_openai_client(settings)
    tool_trace: list[ToolTrace] = []
    observations: list[dict[str, Any]] = []
    collected_hits: dict[str, ClusterHit] = {}
    collected_quotes: dict[str, QuoteRecord] = {}
    collected_trends: dict[str, TrendSnippet] = {}
    comparisons: list[dict[str, Any]] = []
    report_sections: list[str] = []

    for step in range(settings.llm_max_tool_steps):
        plan = client.complete_json(
            _planner_messages(question, chat_history, observations, step),
            max_tokens=700,
            temperature=0.1,
        )
        actions = _normalize_actions(plan.get("actions", []))
        if not actions:
            if step == 0:
                actions = _default_actions(question)
            else:
                break

        for action in actions[:2]:
            result = _execute_tool_action(retriever, action)
            tool_trace.append(result["trace"])
            observations.append(result["planner_view"])
            for hit in result.get("hits", []):
                previous = collected_hits.get(hit.cluster_id)
                if previous is None or hit.score > previous.score:
                    collected_hits[hit.cluster_id] = hit
            for quote in result.get("quotes", []):
                collected_quotes[quote.review_id] = quote
            for trend in result.get("trends", []):
                collected_trends[trend.cluster_id] = trend
            if result.get("comparison") is not None:
                comparisons.append(result["comparison"])
            if result.get("report_section"):
                report_sections.append(str(result["report_section"]))

        if plan.get("ready_to_answer") and collected_hits:
            break

    if not collected_hits:
        fallback_actions = _default_actions(question)
        for action in fallback_actions:
            result = _execute_tool_action(retriever, action)
            tool_trace.append(result["trace"])
            observations.append(result["planner_view"])
            for hit in result.get("hits", []):
                collected_hits[hit.cluster_id] = hit

    if not collected_hits:
        raise PFIAError(
            "NO_EVIDENCE_AVAILABLE",
            "No grounded evidence found for this question.",
            status_code=404,
        )

    top_hits = sorted(
        collected_hits.values(),
        key=lambda item: (-item.score, -item.priority_score, item.cluster_id),
    )[:3]
    for hit in top_hits[:2]:
        if not any(
            quote.cluster_id == hit.cluster_id for quote in collected_quotes.values()
        ):
            quote_result = _execute_tool_action(
                retriever,
                {
                    "tool": "get_quotes",
                    "arguments": {"cluster_id": hit.cluster_id, "limit": 2},
                },
            )
            tool_trace.append(quote_result["trace"])
            observations.append(quote_result["planner_view"])
            for quote in quote_result.get("quotes", []):
                collected_quotes[quote.review_id] = quote

    if TREND_RE.search(question):
        for hit in top_hits[:2]:
            if hit.cluster_id in collected_trends:
                continue
            trend_result = _execute_tool_action(
                retriever,
                {"tool": "get_trend", "arguments": {"cluster_id": hit.cluster_id}},
            )
            tool_trace.append(trend_result["trace"])
            observations.append(trend_result["planner_view"])
            for trend in trend_result.get("trends", []):
                collected_trends[trend.cluster_id] = trend

    evidence = EvidenceBundle(
        query=question,
        cluster_hits=top_hits,
        quotes=list(collected_quotes.values())[:4],
        trends=list(collected_trends.values())[:3],
        context_tokens_estimate=_estimate_context_tokens(
            question,
            top_hits,
            list(collected_quotes.values()),
            list(collected_trends.values()),
        ),
    )

    writer_result = client.complete_json(
        _writer_messages(
            question,
            chat_history,
            evidence,
            comparisons,
            report_sections,
        ),
        max_tokens=900,
        temperature=0.1,
    )
    answer_text = _normalize_writer_answer(writer_result.get("answer"))
    if not answer_text:
        answer_text = _compose_answer(
            question,
            evidence,
            comparison_payload=comparisons[0] if comparisons else None,
            trend_notes=[
                f"`{trend.cluster_id}` is {trend.note} ({trend.trend_delta:+.2f})."
                for trend in evidence.trends
            ],
            report_snippet=report_sections[0] if report_sections else "",
        )
    return ChatAnswer(
        answer=answer_text,
        evidence=evidence,
        tool_trace=tool_trace,
        degraded_mode=False,
    )


def _planner_messages(
    question: str,
    chat_history: list[dict[str, str]],
    observations: list[dict[str, Any]],
    step: int,
) -> list[dict[str, str]]:
    """Build the prompt for the OpenAI query-planner agent."""
    recent_history = chat_history[-3:]
    observation_payload = observations[-6:]
    return [
        {
            "role": "system",
            "content": (
                "You are QueryPlannerAgent for PFIA. "
                "Plan grounded retrieval for a product-feedback question. "
                "Return strict JSON with keys: ready_to_answer, actions, notes. "
                "Each action must contain tool and arguments. "
                "Allowed tools: top_clusters, search_clusters, get_quotes, get_trend, compare_clusters, get_report_section. "
                "Use at most 2 actions per step. "
                "If the question is about the highest-priority issue, start with top_clusters. "
                "Do not invent cluster ids. Use tool outputs from prior observations."
            ),
        },
        {
            "role": "user",
            "content": json.dumps(
                {
                    "step": step + 1,
                    "question": question,
                    "recent_chat_history": recent_history,
                    "observations": observation_payload,
                },
                ensure_ascii=False,
            ),
        },
    ]


def _writer_messages(
    question: str,
    chat_history: list[dict[str, str]],
    evidence: EvidenceBundle,
    comparisons: list[dict[str, Any]],
    report_sections: list[str],
) -> list[dict[str, str]]:
    """Build the prompt for the OpenAI answer-writer agent."""
    return [
        {
            "role": "system",
            "content": (
                "You are AnswerWriterAgent for PFIA. "
                "Write a concise grounded answer for a product manager. "
                "Use only the provided evidence. "
                "Return strict JSON with key 'answer'. "
                "Mention cluster ids when citing evidence. "
                "If evidence is limited, say so instead of guessing."
            ),
        },
        {
            "role": "user",
            "content": json.dumps(
                {
                    "question": question,
                    "recent_chat_history": chat_history[-3:],
                    "evidence": evidence.model_dump(mode="json"),
                    "comparisons": comparisons[:1],
                    "report_sections": report_sections[:1],
                },
                ensure_ascii=False,
            ),
        },
    ]


def _normalize_actions(actions: Any) -> list[dict[str, Any]]:
    """Validate and normalize planner actions into executable tool calls."""
    if not isinstance(actions, list):
        return []
    normalized: list[dict[str, Any]] = []
    allowed_tools = {
        tool.value
        for tool in (
            ToolName.top_clusters,
            ToolName.search_clusters,
            ToolName.get_quotes,
            ToolName.get_trend,
            ToolName.compare_clusters,
            ToolName.get_report_section,
        )
    }
    for action in actions:
        if not isinstance(action, dict):
            continue
        tool = str(action.get("tool", "")).strip()
        arguments = action.get("arguments", {})
        if tool not in allowed_tools or not isinstance(arguments, dict):
            continue
        normalized.append({"tool": tool, "arguments": arguments})
    return normalized


def _default_actions(question: str) -> list[dict[str, Any]]:
    """Return a safe deterministic bootstrap plan when the planner is empty."""
    if PRIORITY_RE.search(question):
        return [{"tool": "top_clusters", "arguments": {"top_k": 3}}]
    return [{"tool": "search_clusters", "arguments": {"query": question, "top_k": 3}}]


def _execute_tool_action(
    retriever: SessionRetriever, action: dict[str, Any]
) -> dict[str, Any]:
    """Execute one retrieval action and convert it into traceable outputs."""
    tool = action["tool"]
    arguments = action["arguments"]

    try:
        if tool == ToolName.top_clusters.value:
            top_k = max(1, min(int(arguments.get("top_k", 3)), 10))
            hits = retriever.top_clusters(top_k=top_k)
            return {
                "trace": ToolTrace(
                    tool=ToolName.top_clusters,
                    input={"top_k": top_k},
                    output_summary=f"Selected {len(hits)} highest-priority clusters.",
                ),
                "planner_view": {
                    "tool": tool,
                    "result": [
                        {
                            "cluster_id": hit.cluster_id,
                            "label": hit.label,
                            "priority_score": hit.priority_score,
                        }
                        for hit in hits[:3]
                    ],
                },
                "hits": hits,
            }

        if tool == ToolName.search_clusters.value:
            query = str(arguments.get("query", "")).strip()
            top_k = max(1, min(int(arguments.get("top_k", 3)), 10))
            hits = retriever.search_clusters(query, top_k=top_k)
            return {
                "trace": ToolTrace(
                    tool=ToolName.search_clusters,
                    input={"query": query, "top_k": top_k},
                    output_summary=f"Found {len(hits)} cluster hits for the query.",
                ),
                "planner_view": {
                    "tool": tool,
                    "result": [
                        {
                            "cluster_id": hit.cluster_id,
                            "label": hit.label,
                            "score": hit.score,
                        }
                        for hit in hits[:3]
                    ],
                },
                "hits": hits,
            }

        if tool == ToolName.get_quotes.value:
            cluster_id = str(arguments.get("cluster_id", "")).strip()
            limit = max(1, min(int(arguments.get("limit", 2)), 5))
            quotes = retriever.get_quotes(cluster_id, limit=limit)
            return {
                "trace": ToolTrace(
                    tool=ToolName.get_quotes,
                    input={"cluster_id": cluster_id, "limit": limit},
                    output_summary=f"Loaded {len(quotes)} supporting quotes for {cluster_id}.",
                ),
                "planner_view": {
                    "tool": tool,
                    "result": {
                        "cluster_id": cluster_id,
                        "quotes": [quote.text for quote in quotes[:2]],
                    },
                },
                "quotes": quotes,
            }

        if tool == ToolName.get_trend.value:
            cluster_id = str(arguments.get("cluster_id", "")).strip()
            trend = retriever.get_trend(cluster_id)
            return {
                "trace": ToolTrace(
                    tool=ToolName.get_trend,
                    input={"cluster_id": cluster_id},
                    output_summary=f"Trend for {cluster_id}: {trend.note} ({trend.trend_delta:+.2f}).",
                ),
                "planner_view": {
                    "tool": tool,
                    "result": {
                        "cluster_id": trend.cluster_id,
                        "trend_delta": trend.trend_delta,
                        "recent_count": trend.recent_count,
                        "note": trend.note,
                    },
                },
                "trends": [trend],
            }

        if tool == ToolName.compare_clusters.value:
            cluster_a = str(arguments.get("cluster_a", "")).strip()
            cluster_b = str(arguments.get("cluster_b", "")).strip()
            comparison = retriever.compare_clusters(cluster_a, cluster_b)
            return {
                "trace": ToolTrace(
                    tool=ToolName.compare_clusters,
                    input={"cluster_a": cluster_a, "cluster_b": cluster_b},
                    output_summary=f"Compared {cluster_a} against {cluster_b}.",
                ),
                "planner_view": {
                    "tool": tool,
                    "result": comparison,
                },
                "comparison": comparison,
            }

        if tool == ToolName.get_report_section.value:
            section_name = str(
                arguments.get("section_name", "executive_summary")
            ).strip()
            section = retriever.get_report_section(section_name)
            return {
                "trace": ToolTrace(
                    tool=ToolName.get_report_section,
                    input={"section_name": section_name},
                    output_summary=f"Loaded report section {section_name}.",
                ),
                "planner_view": {
                    "tool": tool,
                    "result": {"section_name": section_name, "excerpt": section[:300]},
                },
                "report_section": section[:1200],
            }
    except PFIAError as exc:
        enum_tool = ToolName(tool)
        return {
            "trace": ToolTrace(
                tool=enum_tool,
                input=arguments,
                output_summary=f"Tool failed with {exc.code}: {exc.message}",
            ),
            "planner_view": {
                "tool": tool,
                "error": {"code": exc.code, "message": exc.message},
            },
        }

    raise PFIAError(
        "UNKNOWN_TOOL",
        f"Unsupported retrieval tool requested: {tool}.",
        status_code=500,
    )


def _estimate_context_tokens(
    question: str,
    hits: list[ClusterHit],
    quotes: list[QuoteRecord],
    trends: list[TrendSnippet],
) -> int:
    """Estimate prompt size for the answer-writer agent."""
    payload = {
        "question": question,
        "hits": [hit.model_dump(mode="json") for hit in hits],
        "quotes": [quote.model_dump(mode="json") for quote in quotes[:4]],
        "trends": [trend.model_dump(mode="json") for trend in trends[:3]],
    }
    return max(1, len(json.dumps(payload, ensure_ascii=False)) // 4)


def _compose_answer(
    question: str,
    evidence: EvidenceBundle,
    comparison_payload: dict[str, Any] | None,
    trend_notes: list[str],
    report_snippet: str,
) -> str:
    """Compose the local fallback answer from grounded evidence.

    Args:
        question: Original user question.
        evidence: Retrieved evidence bundle.
        comparison_payload: Optional comparison data for two clusters.
        trend_notes: Optional trend remarks to append.
        report_snippet: Optional report section excerpt.

    Returns:
        Rendered answer text.
    """
    _ = question
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


def _normalize_writer_answer(value: Any) -> str:
    """Normalize flexible LLM answer payloads into readable text.

    Args:
        value: Raw ``answer`` field returned by the answer-writer agent.

    Returns:
        Human-readable answer text.
    """
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, dict):
        text = value.get("text")
        if isinstance(text, str) and text.strip():
            return text.strip()

        highest = value.get("highest_priority_issue")
        evidence = value.get("evidence")
        if isinstance(highest, str) and isinstance(evidence, dict):
            lines = [f"The highest-priority issue is {highest}."]
            summary = evidence.get("summary")
            if isinstance(summary, str) and summary.strip():
                lines.append(summary.strip())
            trend = evidence.get("trend")
            if isinstance(trend, dict):
                note = trend.get("note")
                delta = trend.get("trend_delta")
                if isinstance(note, str):
                    if isinstance(delta, (int, float)):
                        lines.append(f"Trend: {note} ({float(delta):+.2f}).")
                    else:
                        lines.append(f"Trend: {note}.")
            quotes = evidence.get("quotes")
            if isinstance(quotes, list) and quotes:
                fragments = []
                for item in quotes[:3]:
                    if not isinstance(item, dict):
                        continue
                    review_id = item.get("review_id")
                    text = item.get("text")
                    if isinstance(text, str):
                        prefix = f"{review_id}: " if isinstance(review_id, str) else ""
                        fragments.append(f'{prefix}"{text}"')
                if fragments:
                    lines.append("Evidence: " + " | ".join(fragments))
            cluster_id = evidence.get("cluster_id")
            if isinstance(cluster_id, str) and cluster_id:
                lines.append(f"Cluster: `{cluster_id}`.")
            return "\n\n".join(lines).strip()

        return json.dumps(value, ensure_ascii=False)
    if isinstance(value, list):
        return json.dumps(value, ensure_ascii=False)
    return "" if value is None else str(value).strip()
