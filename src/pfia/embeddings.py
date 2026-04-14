from __future__ import annotations

import time
from dataclasses import dataclass
from functools import lru_cache
from typing import Any

import httpx
import numpy as np

from pfia.config import Settings
from pfia.errors import PFIAError
from pfia.observability import record_provider_call

try:  # pragma: no cover - optional dependency in CI/runtime
    from sentence_transformers import SentenceTransformer
except Exception:  # pragma: no cover - graceful local fallback
    SentenceTransformer = None


@dataclass
class EmbeddingBatchResult:
    """Normalized result returned by the embedding router."""

    vectors: np.ndarray
    backend_requested: str
    backend_effective: str
    model_effective: str | None = None
    degraded_reason: str | None = None


class OpenAIEmbeddingClient:
    """Small HTTP client for OpenAI-compatible embeddings endpoints."""

    provider_name = "openai"

    def __init__(
        self,
        *,
        api_key: str,
        base_url: str,
        default_model: str,
        timeout_s: float = 30.0,
        max_retries: int = 2,
        http_client: httpx.Client | None = None,
    ) -> None:
        """Store connection parameters for an embeddings provider."""

        self.api_key = api_key.strip()
        self.base_url = base_url.rstrip("/")
        self.default_model = default_model
        self.timeout_s = timeout_s
        self.max_retries = max_retries
        self._http_client = http_client
        self.last_provider_used: str | None = None
        self.last_model_used: str | None = None

    @property
    def available(self) -> bool:
        """Return whether the client has enough configuration to run."""

        return bool(self.api_key and self.default_model)

    def embed_texts(
        self,
        texts: list[str],
        *,
        model: str | None = None,
        batch_size: int = 128,
    ) -> np.ndarray:
        """Embed a batch of texts via the upstream embeddings API."""

        if not self.available:
            raise PFIAError(
                "EMBED_PROVIDER_NOT_CONFIGURED",
                f"{self.provider_name} embeddings were requested, but the API key or model is missing.",
                status_code=500,
            )
        if not texts:
            self.last_provider_used = self.provider_name
            self.last_model_used = model or self.default_model
            return np.zeros((0, 1), dtype=np.float32)

        client = self._http_client or httpx.Client(timeout=self.timeout_s)
        should_close = self._http_client is None
        model_name = model or self.default_model
        batches: list[np.ndarray] = []

        try:
            for start in range(0, len(texts), max(1, batch_size)):
                chunk = texts[start : start + max(1, batch_size)]
                payload = {
                    "model": model_name,
                    "input": chunk,
                }
                for attempt in range(self.max_retries + 1):
                    started = time.perf_counter()
                    try:
                        response = client.post(
                            f"{self.base_url}/embeddings",
                            headers={
                                "Authorization": f"Bearer {self.api_key}",
                                "Content-Type": "application/json",
                            },
                            json=payload,
                        )
                        response.raise_for_status()
                        data = response.json()
                        batches.append(_parse_openai_embeddings(data))
                        record_provider_call(
                            kind="embedding",
                            provider=self.provider_name,
                            model=model_name,
                            status="success",
                            latency_s=time.perf_counter() - started,
                            usage=data.get("usage", {}) or {},
                        )
                        break
                    except (httpx.HTTPError, ValueError) as exc:
                        record_provider_call(
                            kind="embedding",
                            provider=self.provider_name,
                            model=model_name,
                            status="error",
                            latency_s=time.perf_counter() - started,
                            error_code="EMBED_PROVIDER_UNAVAILABLE",
                        )
                        retryable = (
                            attempt < self.max_retries and _is_retryable_http_error(exc)
                        )
                        if retryable:
                            time.sleep(0.5 * (attempt + 1))
                            continue
                        raise PFIAError(
                            "EMBED_PROVIDER_UNAVAILABLE",
                            f"{self.provider_name} embeddings request failed: {exc}",
                            status_code=502,
                            retryable=False,
                        ) from exc
        finally:
            if should_close:
                client.close()

        vectors = (
            np.vstack(batches).astype(np.float32)
            if batches
            else np.zeros((0, 1), dtype=np.float32)
        )
        vectors = _l2_normalize(vectors)
        self.last_provider_used = self.provider_name
        self.last_model_used = model_name
        return vectors


class SentenceTransformerEmbeddingClient:
    """Local sentence-transformers embedding client."""

    provider_name = "sentence-transformers"

    def __init__(
        self,
        *,
        default_model: str,
    ) -> None:
        """Store the local sentence-transformer model identifier."""

        self.default_model = default_model
        self.last_provider_used: str | None = None
        self.last_model_used: str | None = None

    @property
    def available(self) -> bool:
        """Return whether sentence-transformers is importable and configured."""

        return bool(self.default_model and sentence_transformers_available())

    def embed_texts(
        self,
        texts: list[str],
        *,
        model: str | None = None,
        batch_size: int = 128,
    ) -> np.ndarray:
        """Embed texts with a cached local sentence-transformer model."""

        model_name = model or self.default_model
        if not model_name or not sentence_transformers_available():
            raise PFIAError(
                "EMBED_PROVIDER_NOT_CONFIGURED",
                "sentence-transformers embeddings were requested, but the package or model is missing.",
                status_code=500,
            )
        if not texts:
            self.last_provider_used = self.provider_name
            self.last_model_used = model_name
            return np.zeros((0, 1), dtype=np.float32)

        started = time.perf_counter()
        encoder = _load_sentence_transformer(model_name)
        vectors = encoder.encode(
            texts,
            batch_size=max(1, batch_size),
            convert_to_numpy=True,
            normalize_embeddings=True,
            show_progress_bar=False,
        )
        vectors = np.asarray(vectors, dtype=np.float32)
        if vectors.ndim == 1:
            vectors = vectors.reshape(1, -1)
        vectors = _l2_normalize(vectors)
        self.last_provider_used = self.provider_name
        self.last_model_used = model_name
        record_provider_call(
            kind="embedding",
            provider=self.provider_name,
            model=model_name,
            status="success",
            latency_s=time.perf_counter() - started,
            usage={"input_tokens": 0},
        )
        return vectors


class FallbackEmbeddingClient:
    """Route embedding requests across primary and fallback providers."""

    def __init__(
        self, *, primary: Any | None, fallbacks: list[Any] | None = None
    ) -> None:
        """Store the provider chain in call order."""

        self.primary = primary
        self.fallbacks = fallbacks or []
        self.last_provider_used: str | None = None
        self.last_model_used: str | None = None

    @property
    def available(self) -> bool:
        """Return whether any provider in the chain is callable."""

        return any(
            client is not None and client.available for client in self._clients()
        )

    def embed_texts(
        self,
        texts: list[str],
        *,
        model: str | None = None,
        batch_size: int = 128,
    ) -> np.ndarray:
        """Embed texts with fallback routing across providers."""

        last_error: PFIAError | None = None
        for client in self._clients():
            if client is None or not client.available:
                continue
            try:
                vectors = client.embed_texts(
                    texts,
                    model=model,
                    batch_size=batch_size,
                )
                self.last_provider_used = client.last_provider_used or getattr(
                    client, "provider_name", None
                )
                self.last_model_used = client.last_model_used or getattr(
                    client, "default_model", None
                )
                return vectors
            except PFIAError as exc:
                last_error = exc
                continue
        raise last_error or PFIAError(
            "EMBED_PROVIDER_NOT_CONFIGURED",
            "No embedding provider is configured.",
            status_code=500,
        )

    def _clients(self) -> tuple[Any | None, ...]:
        """Return provider clients in the configured call order."""

        return (self.primary, *self.fallbacks)


def sentence_transformers_available() -> bool:
    """Return whether ``sentence-transformers`` is importable."""

    return SentenceTransformer is not None


def build_embedding_client(
    settings: Settings,
    *,
    http_client: httpx.Client | None = None,
    backend_override: str | None = None,
    model_override: str | None = None,
) -> FallbackEmbeddingClient:
    """Build the embedding router for the requested backend."""

    requested_backend = (
        backend_override or settings.embedding_backend or "local"
    ).lower()
    openai_client = OpenAIEmbeddingClient(
        api_key=settings.openai_api_key,
        base_url=settings.openai_base_url,
        default_model=model_override or settings.embedding_primary_model,
        timeout_s=settings.openai_timeout_s,
        max_retries=settings.openai_max_retries,
        http_client=http_client,
    )
    local_client = SentenceTransformerEmbeddingClient(
        default_model=model_override or settings.embedding_fallback_model,
    )

    if requested_backend == "openai":
        return FallbackEmbeddingClient(
            primary=openai_client if openai_client.available else None,
            fallbacks=[local_client] if local_client.available else [],
        )
    return FallbackEmbeddingClient(
        primary=local_client if local_client.available else None,
        fallbacks=[],
    )


def embed_texts(
    texts: list[str],
    settings: Settings,
    *,
    http_client: httpx.Client | None = None,
    backend_override: str | None = None,
    model_override: str | None = None,
    batch_size: int | None = None,
) -> EmbeddingBatchResult:
    """Embed texts with provider routing and normalized metadata."""

    requested_backend = (
        backend_override or settings.embedding_backend or "local"
    ).lower()
    router = build_embedding_client(
        settings,
        http_client=http_client,
        backend_override=requested_backend,
        model_override=model_override,
    )
    vectors = router.embed_texts(
        texts,
        model=model_override,
        batch_size=batch_size or settings.embedding_batch_size,
    )
    return EmbeddingBatchResult(
        vectors=vectors,
        backend_requested=requested_backend,
        backend_effective=router.last_provider_used or requested_backend,
        model_effective=router.last_model_used,
    )


@lru_cache(maxsize=2)
def _load_sentence_transformer(model_name: str):
    """Load and cache a sentence-transformers model instance."""

    if SentenceTransformer is None:  # pragma: no cover - guarded by availability
        raise RuntimeError("sentence-transformers is not installed")
    return SentenceTransformer(model_name)


def _parse_openai_embeddings(payload: dict[str, Any]) -> np.ndarray:
    """Normalize an embeddings API payload into a dense matrix."""

    items = payload.get("data") or []
    if not items:
        raise PFIAError(
            "EMBED_PROVIDER_UNAVAILABLE",
            "Embedding provider returned no vectors.",
            status_code=502,
        )
    ordered = sorted(
        (
            item
            for item in items
            if isinstance(item, dict) and isinstance(item.get("embedding"), list)
        ),
        key=lambda item: int(item.get("index", 0)),
    )
    if not ordered:
        raise PFIAError(
            "EMBED_PROVIDER_UNAVAILABLE",
            "Embedding provider returned malformed vectors.",
            status_code=502,
        )
    return np.asarray([item["embedding"] for item in ordered], dtype=np.float32)


def _l2_normalize(vectors: np.ndarray) -> np.ndarray:
    """Normalize dense vectors row-wise for cosine-compatible retrieval."""

    if vectors.size == 0:
        return vectors.astype(np.float32)
    norms = np.linalg.norm(vectors, axis=1, keepdims=True)
    norms[norms == 0.0] = 1.0
    return (vectors / norms).astype(np.float32)


def _is_retryable_http_error(exc: Exception) -> bool:
    """Return whether the upstream embedding failure is worth retrying."""

    if isinstance(exc, httpx.TimeoutException):
        return True
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code in {408, 409, 429, 500, 502, 503, 504}
    if isinstance(exc, httpx.NetworkError):
        return True
    return False
