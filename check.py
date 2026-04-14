from __future__ import annotations

import argparse
import mimetypes
import time
from pathlib import Path

import httpx


def parse_args() -> argparse.Namespace:
    """Parse command-line arguments for the PFIA smoke runner."""
    parser = argparse.ArgumentParser(
        description="Run a PFIA upload -> process -> chat smoke check."
    )
    parser.add_argument(
        "--base-url",
        default="http://127.0.0.1:8000",
        help="Base URL of the running PFIA service.",
    )
    parser.add_argument(
        "--file",
        default="data/demo/mobile_app_reviews.csv",
        help="Path to a CSV or JSON file to upload.",
    )
    parser.add_argument(
        "--question",
        default="What is the highest-priority issue and what evidence supports it?",
        help="Grounded Q&A question to ask after the batch completes.",
    )
    parser.add_argument(
        "--poll-attempts",
        type=int,
        default=90,
        help="Maximum number of polling attempts before giving up.",
    )
    parser.add_argument(
        "--poll-interval",
        type=float,
        default=1.0,
        help="Seconds to wait between polling attempts.",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=60.0,
        help="HTTP timeout for each request in seconds.",
    )
    return parser.parse_args()


def detect_content_type(path: Path) -> str:
    """Infer the upload content type from the local file path."""
    guessed, _ = mimetypes.guess_type(str(path))
    return guessed or "application/octet-stream"


def main() -> None:
    """Run a full PFIA smoke check against a live service."""
    args = parse_args()
    base_url = args.base_url.rstrip("/")
    upload_path = Path(args.file)
    if not upload_path.exists():
        raise SystemExit(f"Input file does not exist: {upload_path}")

    with upload_path.open("rb") as handle:
        response = httpx.post(
            f"{base_url}/api/sessions/upload",
            files={
                "file": (
                    upload_path.name,
                    handle,
                    detect_content_type(upload_path),
                )
            },
            timeout=args.timeout,
        )
    print("UPLOAD_STATUS:", response.status_code)
    print("UPLOAD_BODY:", response.text)
    response.raise_for_status()
    payload = response.json()
    session_id = payload["session_id"]

    detail = None
    for attempt in range(args.poll_attempts):
        detail_response = httpx.get(
            f"{base_url}/api/sessions/{session_id}",
            timeout=args.timeout,
        )
        detail_response.raise_for_status()
        detail = detail_response.json()
        print(
            "POLL:",
            attempt,
            detail["session"]["status"],
            detail["job"]["status"],
            detail["job"]["stage"],
        )
        if detail["session"]["status"] in {"COMPLETED", "DEGRADED_COMPLETED", "FAILED"}:
            break
        time.sleep(args.poll_interval)

    if detail is None:
        raise SystemExit("No session detail received from the PFIA service.")

    print("FINAL_SESSION_STATUS:", detail["session"]["status"])
    print("FINAL_JOB_STATUS:", detail["job"]["status"])
    runtime_metadata = detail.get("runtime_metadata") or {}
    print("RUNTIME_PROFILE:", runtime_metadata.get("runtime_profile", "n/a"))
    print("TRACE_CORRELATION_ID:", runtime_metadata.get("trace_correlation_id", "n/a"))
    print(
        "TRACE_EXPORTERS:",
        runtime_metadata.get("trace_exporters_effective", []),
    )
    print("TRACE_LOCAL_PATH:", runtime_metadata.get("trace_local_path", "n/a"))
    print(
        "ORCHESTRATOR_BACKEND:",
        runtime_metadata.get("orchestrator_backend_effective", "n/a"),
    )
    print(
        "GENERATION_BACKEND:",
        runtime_metadata.get("generation_backend_effective", "n/a"),
    )
    print(
        "RETRIEVAL_BACKEND:",
        runtime_metadata.get("retrieval_backend_effective", "n/a"),
    )
    print("PII_BACKEND:", runtime_metadata.get("pii_backend_effective", "n/a"))
    print(
        "SENTIMENT_BACKEND:",
        runtime_metadata.get("sentiment_backend_effective", "n/a"),
    )
    print(
        "SENTIMENT_MODEL:",
        runtime_metadata.get("sentiment_model_effective", "n/a"),
    )
    print(
        "EMBEDDING_BACKEND:",
        runtime_metadata.get("embedding_backend_effective")
        or runtime_metadata.get("embedding_backend", "n/a"),
    )
    print(
        "EMBEDDING_MODEL:",
        runtime_metadata.get("embedding_model_effective", "n/a"),
    )
    print("LLM_CALL_COUNT:", runtime_metadata.get("llm_call_count", 0))
    print("EMBEDDING_CALL_COUNT:", runtime_metadata.get("embedding_call_count", 0))
    print("PROMPT_TOKENS_TOTAL:", runtime_metadata.get("prompt_tokens_total", 0))
    print(
        "COMPLETION_TOKENS_TOTAL:",
        runtime_metadata.get("completion_tokens_total", 0),
    )
    print(
        "EMBEDDING_INPUT_TOKENS_TOTAL:",
        runtime_metadata.get("embedding_input_tokens_total", 0),
    )
    print("ESTIMATED_COST_USD:", runtime_metadata.get("estimated_cost_usd", 0.0))
    print("PRIMARY_MODEL:", runtime_metadata.get("llm_primary_model", "n/a"))
    print("INPUT_FILENAME:", runtime_metadata.get("input_filename", "n/a"))
    print("TOP_CLUSTER_IDS:", runtime_metadata.get("top_cluster_ids", []))

    chat = httpx.post(
        f"{base_url}/api/sessions/{session_id}/chat",
        json={"question": args.question},
        timeout=args.timeout,
    )
    print("CHAT_STATUS:", chat.status_code)
    print("CHAT_BODY:", chat.text)
    chat.raise_for_status()


if __name__ == "__main__":
    main()
