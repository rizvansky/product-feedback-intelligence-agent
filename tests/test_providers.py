from __future__ import annotations

from pathlib import Path

import numpy as np

from pfia.config import Settings
from pfia.observability import SessionRunObserver, bind_observer
from pfia.services import build_app_context
from pfia.tracing import LocalJsonlTraceSink, make_trace_record
from pfia.embeddings import FallbackEmbeddingClient, OpenAIEmbeddingClient
from pfia.errors import PFIAError
from pfia.openai_client import AnthropicClient, FallbackRoutingClient, OpenAIClient


def test_provider_router_falls_back_to_mistral():
    """Verify that the routed LLM client falls back from OpenAI to Mistral."""

    class FailingPrimary:
        """Fake failing primary provider."""

        available = True
        default_model = "gpt-4o-mini"
        provider_name = "openai"
        last_provider_used = None
        last_model_used = None

        def complete_json(
            self, messages, *, model=None, temperature=0.1, max_tokens=1200
        ):
            _ = messages, model, temperature, max_tokens
            raise PFIAError("LLM_UPSTREAM_FAILED", "OpenAI failed.", status_code=502)

    class WorkingFallback:
        """Fake working fallback provider."""

        available = True
        default_model = "mistral-small-latest"
        provider_name = "mistral"
        last_provider_used = None
        last_model_used = None

        def complete_json(
            self, messages, *, model=None, temperature=0.1, max_tokens=1200
        ):
            _ = messages, model, temperature, max_tokens
            self.last_provider_used = "mistral"
            self.last_model_used = self.default_model
            return {"answer": "Fallback provider succeeded."}

    router = FallbackRoutingClient(
        primary=FailingPrimary(),
        fallbacks=[WorkingFallback()],
    )
    payload = router.complete_json(
        [{"role": "user", "content": "Return JSON."}],
        max_tokens=100,
        temperature=0.1,
    )

    assert payload["answer"] == "Fallback provider succeeded."
    assert router.last_provider_used == "mistral"
    assert router.last_model_used == "mistral-small-latest"


def test_provider_router_falls_back_to_anthropic_after_mistral():
    """Verify that the router can fall through to Anthropic after other providers fail."""

    class FailingProvider:
        """Fake failing provider for router-chain tests."""

        def __init__(self, provider_name: str, default_model: str) -> None:
            self.available = True
            self.default_model = default_model
            self.provider_name = provider_name
            self.last_provider_used = None
            self.last_model_used = None

        def complete_json(
            self, messages, *, model=None, temperature=0.1, max_tokens=1200
        ):
            _ = messages, model, temperature, max_tokens
            raise PFIAError(
                "LLM_UPSTREAM_FAILED",
                f"{self.provider_name} failed.",
                status_code=502,
            )

    class WorkingAnthropic:
        """Fake Anthropic fallback provider."""

        available = True
        default_model = "claude-3-5-haiku-latest"
        provider_name = "anthropic"
        last_provider_used = None
        last_model_used = None

        def complete_json(
            self, messages, *, model=None, temperature=0.1, max_tokens=1200
        ):
            _ = messages, model, temperature, max_tokens
            self.last_provider_used = "anthropic"
            self.last_model_used = self.default_model
            return {"answer": "Anthropic fallback succeeded."}

    router = FallbackRoutingClient(
        primary=FailingProvider("openai", "gpt-4o-mini"),
        fallbacks=[
            FailingProvider("mistral", "mistral-small-latest"),
            WorkingAnthropic(),
        ],
    )
    payload = router.complete_json(
        [{"role": "user", "content": "Return JSON."}],
        max_tokens=100,
        temperature=0.1,
    )

    assert payload["answer"] == "Anthropic fallback succeeded."
    assert router.last_provider_used == "anthropic"
    assert router.last_model_used == "claude-3-5-haiku-latest"


def test_anthropic_client_parses_messages_response():
    """Verify that Anthropic client normalizes the Messages API payload."""

    class FakeResponse:
        """Minimal fake HTTP response for Anthropic client tests."""

        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, object]:
            return {
                "model": "claude-3-5-haiku-latest",
                "content": [
                    {
                        "type": "text",
                        "text": '{"answer":"Anthropic JSON works."}',
                    }
                ],
                "stop_reason": "end_turn",
                "usage": {"input_tokens": 12, "output_tokens": 5},
            }

    class FakeHttpClient:
        """Minimal fake HTTP client for Anthropic adapter tests."""

        def post(self, url, headers=None, json=None):
            _ = url, headers, json
            return FakeResponse()

    client = AnthropicClient(
        api_key="test-key",
        base_url="https://api.anthropic.com/v1",
        default_model="claude-3-5-haiku-latest",
        http_client=FakeHttpClient(),
    )
    payload = client.complete_json(
        [{"role": "user", "content": "Return JSON only."}],
        max_tokens=100,
        temperature=0.1,
    )

    assert payload["answer"] == "Anthropic JSON works."
    assert client.last_provider_used == "anthropic"
    assert client.last_model_used == "claude-3-5-haiku-latest"


def test_embedding_router_falls_back_to_sentence_transformers():
    """Verify that the embedding router can fall back to a local model."""

    class FailingPrimary:
        """Fake failing primary embedding provider."""

        available = True
        default_model = "text-embedding-3-small"
        provider_name = "openai"
        last_provider_used = None
        last_model_used = None

        def embed_texts(self, texts, *, model=None, batch_size=128):
            _ = texts, model, batch_size
            raise PFIAError(
                "EMBED_PROVIDER_UNAVAILABLE",
                "OpenAI embeddings failed.",
                status_code=502,
            )

    class WorkingFallback:
        """Fake local embedding fallback provider."""

        available = True
        default_model = "paraphrase-multilingual-mpnet-base-v2"
        provider_name = "sentence-transformers"
        last_provider_used = None
        last_model_used = None

        def embed_texts(self, texts, *, model=None, batch_size=128):
            _ = model, batch_size
            self.last_provider_used = self.provider_name
            self.last_model_used = self.default_model
            return np.tile(np.array([[1.0, 0.0]], dtype=np.float32), (len(texts), 1))

    router = FallbackEmbeddingClient(
        primary=FailingPrimary(),
        fallbacks=[WorkingFallback()],
    )
    vectors = router.embed_texts(["hello world"])

    assert vectors.shape == (1, 2)
    assert router.last_provider_used == "sentence-transformers"
    assert router.last_model_used == "paraphrase-multilingual-mpnet-base-v2"


def test_openai_embedding_client_parses_embeddings_response():
    """Verify that OpenAI embedding client normalizes the embeddings payload."""

    class FakeResponse:
        """Minimal fake HTTP response for embedding client tests."""

        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, object]:
            return {
                "data": [
                    {"index": 1, "embedding": [0.0, 1.0]},
                    {"index": 0, "embedding": [1.0, 0.0]},
                ]
            }

    class FakeHttpClient:
        """Minimal fake HTTP client for embedding adapter tests."""

        def post(self, url, headers=None, json=None):
            _ = url, headers, json
            return FakeResponse()

    client = OpenAIEmbeddingClient(
        api_key="test-key",
        base_url="https://api.openai.com/v1",
        default_model="text-embedding-3-small",
        http_client=FakeHttpClient(),
    )
    vectors = client.embed_texts(["first", "second"])

    assert vectors.shape == (2, 2)
    assert np.allclose(vectors[0], np.array([1.0, 0.0], dtype=np.float32))
    assert client.last_provider_used == "openai"
    assert client.last_model_used == "text-embedding-3-small"


def test_provider_observability_records_metrics_and_events(tmp_path):
    """Verify provider calls populate Prometheus metrics and stage events."""

    settings = Settings(
        data_dir=tmp_path / "runtime",
        openai_api_key="test-key",
        generation_backend="openai",
        _env_file=None,
    )
    context = build_app_context(settings)
    context.repo.create_session_and_job(
        "sess_obs",
        "job_obs",
        {"upload_path": str(Path("demo.csv")), "filename": "demo.csv"},
    )
    observer = SessionRunObserver(
        repo=context.repo,
        metrics=context.metrics,
        trace_sink=context.trace_sink,
        session_id="sess_obs",
        job_id="job_obs",
        correlation_id="corr_obs",
    )

    class FakeChatResponse:
        """Minimal fake HTTP response for OpenAI chat tests."""

        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, object]:
            return {
                "model": "gpt-4o-mini",
                "choices": [
                    {
                        "message": {"content": '{"answer":"ok"}'},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {"prompt_tokens": 12, "completion_tokens": 4},
            }

    class FakeEmbeddingResponse:
        """Minimal fake HTTP response for embedding tests."""

        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, object]:
            return {
                "data": [{"index": 0, "embedding": [1.0, 0.0]}],
                "usage": {"total_tokens": 9},
            }

    class FakeHttpClient:
        """Minimal fake HTTP client that serves chat and embedding endpoints."""

        def post(self, url, headers=None, json=None):
            _ = headers, json
            if url.endswith("/chat/completions"):
                return FakeChatResponse()
            if url.endswith("/embeddings"):
                return FakeEmbeddingResponse()
            raise AssertionError(f"Unexpected URL: {url}")

    llm_client = OpenAIClient(
        api_key="test-key",
        base_url="https://api.openai.com/v1",
        default_model="gpt-4o-mini",
        http_client=FakeHttpClient(),
    )
    embedding_client = OpenAIEmbeddingClient(
        api_key="test-key",
        base_url="https://api.openai.com/v1",
        default_model="text-embedding-3-small",
        http_client=FakeHttpClient(),
    )

    with bind_observer(observer):
        llm_client.complete_json([{"role": "user", "content": "Return JSON."}])
        embedding_client.embed_texts(["hello world"])

    metrics_payload = context.metrics.render().decode("utf-8")
    assert "pfia_llm_calls_total" in metrics_payload
    assert "pfia_llm_calls_total{" in metrics_payload
    assert 'provider="openai"' in metrics_payload
    assert 'model="gpt-4o-mini"' in metrics_payload
    assert 'operation="llm"' in metrics_payload
    assert 'status="success"' in metrics_payload
    assert "pfia_embedding_calls_total" in metrics_payload
    assert 'model="text-embedding-3-small"' in metrics_payload

    events = context.repo.get_job_events("sess_obs")
    provider_events = [
        event for event in events if event["event"].startswith("provider.")
    ]
    assert provider_events
    assert all(event["correlation_id"] == "corr_obs" for event in provider_events)
    assert any(event["event"] == "provider.llm" for event in provider_events)
    assert any(event["event"] == "provider.embedding" for event in provider_events)
    trace_path = settings.traces_dir / "events.jsonl"
    trace_content = trace_path.read_text(encoding="utf-8")
    assert '"correlation_id": "corr_obs"' in trace_content
    assert '"event": "provider.llm"' in trace_content
    assert '"event": "provider.embedding"' in trace_content


def test_local_jsonl_trace_sink_writes_trace_records(tmp_path):
    """Verify the always-on local trace sink appends JSONL records."""

    sink = LocalJsonlTraceSink(tmp_path / "traces" / "events.jsonl")
    record = make_trace_record(
        correlation_id="corr_demo",
        session_id="sess_demo",
        job_id="job_demo",
        stage="QNA",
        event="qna.generate",
        level="INFO",
        message="Generated grounded answer.",
        metadata={"degraded_mode": False},
    )

    sink.emit(record)

    payload = (tmp_path / "traces" / "events.jsonl").read_text(encoding="utf-8")
    assert '"correlation_id": "corr_demo"' in payload
    assert '"event": "qna.generate"' in payload
