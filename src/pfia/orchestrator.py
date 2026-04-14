from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any, TypedDict

from pfia.errors import PFIAError
from pfia.models import JobStage, JobStatus, SessionStatus

try:  # pragma: no cover - optional import is validated by integration tests
    from langgraph.graph import END, START, StateGraph
except Exception:  # pragma: no cover - graceful fallback when unavailable
    END = START = StateGraph = None

if TYPE_CHECKING:
    from pfia.models import SessionRecord
    from pfia.services import PFIAService


class JobGraphState(TypedDict, total=False):
    """State carried through the LangGraph batch orchestrator."""

    job_id: str
    session: Any
    upload_path: Path
    reviews: list[Any]
    summary: Any
    preprocess_agent_meta: dict[str, Any]
    preprocessing_runtime_meta: dict[str, Any]
    analysis: Any
    degraded_mode: bool
    report_path: Path


def langgraph_available() -> bool:
    """Return whether the LangGraph runtime is importable."""

    return StateGraph is not None and START is not None and END is not None


class JobLangGraphOrchestrator:
    """LangGraph-backed runtime graph for one PFIA batch job."""

    def __init__(self, service: PFIAService):
        """Bind the orchestrator to a service facade."""

        self.service = service
        self._graph = self._build_graph()

    def run(self, job_id: str, session: SessionRecord) -> JobGraphState:
        """Execute the batch pipeline through the compiled LangGraph."""

        if not langgraph_available():
            raise RuntimeError("LangGraph runtime is not available.")
        return self._graph.invoke({"job_id": job_id, "session": session})

    def _build_graph(self):
        """Compile the sequential PFIA batch graph."""

        if not langgraph_available():
            raise RuntimeError("LangGraph runtime is not available.")
        graph = StateGraph(JobGraphState)
        graph.add_node("validate_input", self._validate_input)
        graph.add_node("preprocess", self._preprocess)
        graph.add_node("analyze", self._analyze)
        graph.add_node("report", self._report)
        graph.add_node("finalize", self._finalize)
        graph.add_edge(START, "validate_input")
        graph.add_edge("validate_input", "preprocess")
        graph.add_edge("preprocess", "analyze")
        graph.add_edge("analyze", "report")
        graph.add_edge("report", "finalize")
        graph.add_edge("finalize", END)
        return graph.compile()

    def _validate_input(self, state: JobGraphState) -> JobGraphState:
        """Validate the uploaded file and move the session into processing."""

        job_id = state["job_id"]
        session = state["session"]
        self.service.repo.set_job_state(
            job_id,
            status=JobStatus.running,
            stage=JobStage.validate_input,
            message="Validating upload",
        )
        self.service.repo.set_session_state(
            session.session_id, status=SessionStatus.processing
        )
        self.service.repo.log_event(
            job_id,
            session.session_id,
            JobStage.validate_input,
            "job.orchestrator.langgraph",
            "INFO",
            "LangGraph orchestrator started.",
        )
        self.service.repo.log_event(
            job_id,
            session.session_id,
            JobStage.validate_input,
            "job.validate.start",
            "INFO",
            "Validating upload.",
        )
        upload_path = Path(session.config_snapshot["upload_path"])
        if not upload_path.exists():
            raise PFIAError("FAILED_INPUT", "Uploaded file is missing from storage.")
        return {"upload_path": upload_path}

    def _preprocess(self, state: JobGraphState) -> JobGraphState:
        """Run preprocessing and persist sanitized artifacts."""

        (
            reviews,
            summary,
            preprocess_agent_meta,
            preprocessing_runtime_meta,
        ) = self.service._run_preprocess(
            state["job_id"], state["session"].session_id, state["upload_path"]
        )
        return {
            "reviews": reviews,
            "summary": summary,
            "preprocess_agent_meta": preprocess_agent_meta,
            "preprocessing_runtime_meta": preprocessing_runtime_meta,
        }

    def _analyze(self, state: JobGraphState) -> JobGraphState:
        """Run embeddings, clustering, scoring, and anomaly detection."""

        analysis = self.service._run_analysis(
            state["job_id"],
            state["session"].session_id,
            state["reviews"],
            state["summary"],
        )
        return {
            "analysis": analysis,
            "degraded_mode": bool(analysis.degraded_mode),
        }

    def _report(self, state: JobGraphState) -> JobGraphState:
        """Build retrieval artifacts and the final Markdown report."""

        report_path = self.service._run_reporting(
            state["job_id"],
            state["session"],
            state["summary"],
            state["reviews"],
            state["analysis"],
            bool(state.get("degraded_mode")),
            state["preprocess_agent_meta"],
            state["preprocessing_runtime_meta"],
            orchestrator_backend_effective="langgraph",
        )
        return {"report_path": report_path}

    def _finalize(self, state: JobGraphState) -> JobGraphState:
        """Persist final job and session statuses after a successful run."""

        self.service._finalize_success(
            state["job_id"],
            state["session"].session_id,
            degraded_mode=bool(state.get("degraded_mode")),
            report_path=state["report_path"],
        )
        return state
