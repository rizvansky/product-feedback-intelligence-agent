from __future__ import annotations

import pickle
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import numpy as np
from scipy.sparse import hstack
from sklearn.decomposition import TruncatedSVD
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity
from sklearn.preprocessing import Normalizer

from pfia.config import Settings
from pfia.embeddings import embed_texts
from pfia.errors import PFIAError
from pfia.models import (
    ClusterHit,
    ClusterRecord,
    EvidenceBundle,
    QuoteRecord,
    TrendSnippet,
)
from pfia.utils import estimate_tokens, normalize_text, tokenize

try:  # pragma: no cover - optional import is exercised in integration tests
    import chromadb
except Exception:  # pragma: no cover - graceful fallback when unavailable
    chromadb = None


STOPWORDS = sorted(
    {
        "a",
        "an",
        "and",
        "are",
        "as",
        "at",
        "be",
        "for",
        "from",
        "i",
        "in",
        "is",
        "it",
        "my",
        "of",
        "on",
        "or",
        "the",
        "to",
        "with",
        "person",
        "email",
        "phone",
        "url",
        "в",
        "для",
        "и",
        "на",
        "не",
        "по",
        "с",
        "это",
    }
)

CHROMA_CLUSTER_COLLECTION = "pfia_clusters_vector"
CHROMA_REVIEW_COLLECTION = "pfia_reviews_vector"
CHROMA_EMBED_DIM = 128
CHROMA_BATCH_SIZE = 256


@dataclass
class RetrievalBuildResult:
    """Metadata returned after building a session retrieval index."""

    requested_backend: str
    effective_backend: str
    embedding_backend_effective: str = "projection"
    embedding_model_effective: str | None = None
    degraded_reason: str | None = None


@dataclass
class RetrievalIndex:
    """Persisted retrieval payload stored on disk for one session."""

    session_id: str
    word_vectorizer: TfidfVectorizer
    char_vectorizer: TfidfVectorizer
    cluster_matrix: Any
    review_matrix: Any
    clusters: list[dict[str, Any]]
    reviews: list[dict[str, Any]]
    trends: dict[str, dict[str, Any]]
    report_sections: dict[str, str]
    backend_requested: str = "local"
    backend_effective: str = "local"
    chroma_path: str | None = None
    cluster_collection_name: str | None = None
    review_collection_name: str | None = None
    embedding_backend_effective: str = "projection"
    embedding_model_effective: str | None = None
    semantic_projector: Any | None = None
    semantic_normalizer: Any | None = None


def chroma_available() -> bool:
    """Return whether the ``chromadb`` package is importable."""

    return chromadb is not None


def build_retrieval_index(
    session_id: str,
    reviews: list[dict[str, Any]],
    clusters: list[ClusterRecord],
    *,
    report_sections: dict[str, str],
    index_path: Path,
    settings: Settings | None = None,
    retrieval_backend: str = "local",
    chroma_path: Path | None = None,
) -> RetrievalBuildResult:
    """Build and serialize the retrieval index for one completed session.

    Args:
        session_id: Owning session identifier.
        reviews: Review payload already enriched with cluster assignments.
        clusters: Cluster metadata to index.
        report_sections: Report snippets exposed to Q&A tools.
        index_path: Output file path for the serialized index.
        settings: Optional runtime settings used to resolve external embedding providers.
        retrieval_backend: Requested retrieval backend.
        chroma_path: Optional persistent Chroma directory for vector collections.

    Returns:
        Summary of the requested and effective retrieval backend.
    """
    cluster_docs = [
        " ".join(
            filter(
                None,
                [
                    cluster.label,
                    cluster.summary,
                    " ".join(cluster.keywords),
                    " ".join(cluster.sources),
                ],
            )
        )
        for cluster in clusters
    ]
    review_docs = [review["text"] for review in reviews]
    corpus = cluster_docs + review_docs

    word_vectorizer = TfidfVectorizer(
        lowercase=True,
        analyzer="word",
        ngram_range=(1, 2),
        token_pattern=r"(?u)\b\w+\b",
        max_features=4000,
        stop_words=STOPWORDS,
        max_df=0.88,
    )
    char_vectorizer = TfidfVectorizer(
        lowercase=True,
        analyzer="char_wb",
        ngram_range=(3, 5),
        max_features=4000,
    )
    word_matrix = word_vectorizer.fit_transform(corpus)
    char_matrix = char_vectorizer.fit_transform(corpus)
    combined = hstack([word_matrix, char_matrix]).tocsr()
    cluster_matrix = combined[: len(cluster_docs)]
    review_matrix = combined[len(cluster_docs) :]
    projection_embeddings, semantic_projector, semantic_normalizer = (
        _build_semantic_projection(combined)
    )
    trends = {
        cluster.cluster_id: {
            "trend_delta": cluster.trend_delta,
            "recent_count": cluster.size,
            "note": "spiking" if cluster.trend_delta > 0.5 else "stable",
        }
        for cluster in clusters
    }

    payload = RetrievalIndex(
        session_id=session_id,
        word_vectorizer=word_vectorizer,
        char_vectorizer=char_vectorizer,
        cluster_matrix=cluster_matrix,
        review_matrix=review_matrix,
        clusters=[cluster.model_dump() for cluster in clusters],
        reviews=reviews,
        trends=trends,
        report_sections=report_sections,
        backend_requested=retrieval_backend,
        backend_effective="local",
        chroma_path=str(chroma_path) if chroma_path is not None else None,
        embedding_backend_effective="projection",
        embedding_model_effective="tfidf-svd-projection",
        semantic_projector=semantic_projector,
        semantic_normalizer=semantic_normalizer,
    )

    degraded_reason = None
    if retrieval_backend == "chroma":
        if chroma_path is None:
            degraded_reason = "chroma_path_not_configured"
        elif not chroma_available():
            degraded_reason = "chromadb_not_installed"
        else:
            try:
                dense_embeddings = projection_embeddings
                if settings is not None:
                    try:
                        embedding_result = embed_texts(
                            corpus,
                            settings,
                            batch_size=settings.embedding_batch_size,
                        )
                        dense_embeddings = embedding_result.vectors
                        payload.embedding_backend_effective = (
                            embedding_result.backend_effective
                        )
                        payload.embedding_model_effective = (
                            embedding_result.model_effective
                        )
                    except PFIAError as exc:
                        degraded_reason = exc.code.lower()
                cluster_embeddings = dense_embeddings[: len(cluster_docs)]
                review_embeddings = dense_embeddings[len(cluster_docs) :]
                chroma_path.mkdir(parents=True, exist_ok=True)
                _write_chroma_vectors(
                    session_id=session_id,
                    chroma_path=chroma_path,
                    clusters=clusters,
                    cluster_docs=cluster_docs,
                    cluster_embeddings=cluster_embeddings,
                    reviews=reviews,
                    review_embeddings=review_embeddings,
                )
                payload.backend_effective = "chroma"
                payload.cluster_collection_name = CHROMA_CLUSTER_COLLECTION
                payload.review_collection_name = CHROMA_REVIEW_COLLECTION
            except Exception as exc:  # pragma: no cover - defensive fallback
                degraded_reason = f"chroma_unavailable:{exc.__class__.__name__}"

    index_path.parent.mkdir(parents=True, exist_ok=True)
    with index_path.open("wb") as handle:
        pickle.dump(payload, handle)
    return RetrievalBuildResult(
        requested_backend=retrieval_backend,
        effective_backend=payload.backend_effective,
        embedding_backend_effective=payload.embedding_backend_effective,
        embedding_model_effective=payload.embedding_model_effective,
        degraded_reason=degraded_reason,
    )


class SessionRetriever:
    """Retrieval helper over a persisted session-scoped index."""

    def __init__(self, payload: RetrievalIndex, settings: Settings | None = None):
        """Materialize validated cluster models from a retrieval payload.

        Args:
            payload: Deserialized retrieval index.
        """
        self.payload = _hydrate_payload_defaults(payload)
        self.settings = settings
        self.clusters = [
            ClusterRecord.model_validate(cluster) for cluster in self.payload.clusters
        ]
        self.cluster_by_id = {cluster.cluster_id: cluster for cluster in self.clusters}
        self._chroma_client = None

    @classmethod
    def load(
        cls, index_path: Path, settings: Settings | None = None
    ) -> "SessionRetriever":
        """Load a retriever from disk.

        Args:
            index_path: Path to the serialized retrieval index.
            settings: Optional runtime settings used for query-time embedding calls.

        Returns:
            Ready-to-use session retriever.

        Raises:
            PFIAError: If the retrieval index is missing.
        """
        if not index_path.exists():
            raise PFIAError(
                "NO_EVIDENCE_AVAILABLE",
                "Retrieval index is missing for this session.",
                status_code=404,
            )
        with index_path.open("rb") as handle:
            payload = pickle.load(handle)
        return cls(payload, settings=settings)

    def search_clusters(self, query: str, top_k: int = 5) -> list[ClusterHit]:
        """Search clusters using hybrid lexical and semantic scoring.

        Args:
            query: Free-form search query.
            top_k: Maximum number of hits to return.

        Returns:
            Ranked cluster hits.
        """
        normalized_query = normalize_text(query)
        if not normalized_query:
            return []

        if self._can_use_chroma():
            try:
                return self._search_clusters_chroma(normalized_query, top_k=top_k)
            except Exception:  # pragma: no cover - fallback path is deterministic
                pass
        return self._search_clusters_local(normalized_query, top_k=top_k)

    def top_clusters(self, top_k: int = 3) -> list[ClusterHit]:
        """Return the highest-priority clusters without query matching.

        Args:
            top_k: Maximum number of hits to return.

        Returns:
            Ranked cluster hits sorted by priority.
        """
        ranked = sorted(
            self.clusters,
            key=lambda cluster: (
                -cluster.priority_score,
                -cluster.size,
                cluster.cluster_id,
            ),
        )
        return [
            ClusterHit(
                cluster_id=cluster.cluster_id,
                score=cluster.priority_score,
                match_reason="priority",
                label=cluster.label,
                summary=cluster.summary,
                priority_score=cluster.priority_score,
            )
            for cluster in ranked[: min(top_k, 10)]
        ]

    def get_quotes(self, cluster_id: str, limit: int = 3) -> list[QuoteRecord]:
        """Return representative quotes for a cluster.

        Args:
            cluster_id: Cluster identifier.
            limit: Maximum number of quotes to return.

        Returns:
            Ranked quote records for the cluster.
        """
        reviews = [
            review
            for review in self.payload.reviews
            if review["cluster_id"] == cluster_id
        ]
        ranked = sorted(
            reviews,
            key=lambda review: (abs(review["sentiment_score"]), len(review["text"])),
            reverse=True,
        )
        return [
            QuoteRecord(
                review_id=review["review_id"],
                cluster_id=cluster_id,
                text=review["text"],
                source=review["source"],
                created_at=datetime.fromisoformat(review["created_at"]),
            )
            for review in ranked[:limit]
        ]

    def get_trend(self, cluster_id: str) -> TrendSnippet:
        """Return trend metadata for a cluster.

        Args:
            cluster_id: Cluster identifier.

        Returns:
            Trend snippet for the cluster.

        Raises:
            PFIAError: If no trend data exists for the cluster.
        """
        trend = self.payload.trends.get(cluster_id)
        if trend is None:
            raise PFIAError(
                "NO_EVIDENCE_AVAILABLE",
                f"No trend data for cluster {cluster_id}.",
                status_code=404,
            )
        return TrendSnippet(
            cluster_id=cluster_id,
            trend_delta=float(trend["trend_delta"]),
            recent_count=int(trend["recent_count"]),
            note=str(trend["note"]),
            baseline=None,
        )

    def compare_clusters(self, cluster_a: str, cluster_b: str) -> dict[str, Any]:
        """Build a lightweight side-by-side comparison between two clusters.

        Args:
            cluster_a: Left cluster identifier.
            cluster_b: Right cluster identifier.

        Returns:
            Comparison payload used by the Q&A composer.

        Raises:
            PFIAError: If either cluster identifier is unknown.
        """
        left = self.cluster_by_id.get(cluster_a)
        right = self.cluster_by_id.get(cluster_b)
        if left is None or right is None:
            raise PFIAError(
                "NO_EVIDENCE_AVAILABLE",
                "One of the clusters requested for comparison was not found.",
                status_code=404,
            )
        return {
            "cluster_a": {
                "cluster_id": left.cluster_id,
                "label": left.label,
                "priority_score": left.priority_score,
                "sentiment_score": left.sentiment_score,
                "trend_delta": left.trend_delta,
                "size": left.size,
            },
            "cluster_b": {
                "cluster_id": right.cluster_id,
                "label": right.label,
                "priority_score": right.priority_score,
                "sentiment_score": right.sentiment_score,
                "trend_delta": right.trend_delta,
                "size": right.size,
            },
        }

    def get_report_section(self, section_name: str) -> str:
        """Return a stored report section by name.

        Args:
            section_name: Logical section key.

        Returns:
            Stored text snippet or an empty string when missing.
        """
        return self.payload.report_sections.get(section_name, "")

    def build_evidence(
        self,
        query: str,
        *,
        top_k: int = 3,
        quote_limit: int = 2,
        include_trends: bool = True,
    ) -> EvidenceBundle:
        """Assemble the grounded evidence bundle used to answer a question.

        Args:
            query: Free-form user query.
            top_k: Maximum number of cluster hits to keep.
            quote_limit: Maximum quotes per cluster.
            include_trends: Whether trend snippets should be added.

        Returns:
            Evidence bundle for downstream answer generation.

        Raises:
            PFIAError: If the retriever cannot find any grounded evidence.
        """
        hits = self.search_clusters(query, top_k=top_k)
        if not hits:
            raise PFIAError(
                "NO_EVIDENCE_AVAILABLE",
                "Retriever could not find grounded evidence for this question.",
                status_code=404,
            )
        quotes: list[QuoteRecord] = []
        trends: list[TrendSnippet] = []
        for hit in hits[:2]:
            quotes.extend(self.get_quotes(hit.cluster_id, limit=quote_limit))
            if include_trends:
                trends.append(self.get_trend(hit.cluster_id))
        context_tokens = sum(estimate_tokens(hit.summary) for hit in hits) + sum(
            estimate_tokens(quote.text) for quote in quotes
        )
        return EvidenceBundle(
            query=query,
            cluster_hits=hits,
            quotes=quotes[:6],
            trends=trends[:2],
            context_tokens_estimate=context_tokens,
        )

    def _can_use_chroma(self) -> bool:
        """Return whether this payload can query Chroma safely."""

        return bool(
            self.payload.backend_effective == "chroma"
            and self.payload.chroma_path
            and self.payload.cluster_collection_name
            and self.payload.review_collection_name
            and chroma_available()
        )

    def _search_clusters_local(self, query: str, *, top_k: int) -> list[ClusterHit]:
        """Search clusters using persisted local matrices only."""

        query_matrix = self._transform_query(query)
        cluster_semantic = cosine_similarity(
            query_matrix, self.payload.cluster_matrix
        ).ravel()
        review_semantic = cosine_similarity(
            query_matrix, self.payload.review_matrix
        ).ravel()
        review_semantic_by_cluster = self._aggregate_review_scores(review_semantic)
        cluster_semantic_by_cluster = {
            cluster.cluster_id: float(cluster_semantic[index])
            for index, cluster in enumerate(self.clusters)
        }
        return self._rank_cluster_hits(
            query=query,
            cluster_semantic_by_cluster=cluster_semantic_by_cluster,
            review_semantic_by_cluster=review_semantic_by_cluster,
            top_k=top_k,
        )

    def _search_clusters_chroma(self, query: str, *, top_k: int) -> list[ClusterHit]:
        """Search clusters through persistent Chroma collections."""

        query_embedding = self._transform_query_for_chroma(query)
        client = self._get_chroma_client()
        cluster_collection = client.get_collection(self.payload.cluster_collection_name)
        review_collection = client.get_collection(self.payload.review_collection_name)
        cluster_result = cluster_collection.query(
            query_embeddings=[query_embedding],
            n_results=min(max(top_k * 2, 5), 20),
            where={"session_id": self.payload.session_id},
            include=["distances", "metadatas"],
        )
        review_result = review_collection.query(
            query_embeddings=[query_embedding],
            n_results=min(max(top_k * 4, 8), 24),
            where={"session_id": self.payload.session_id},
            include=["distances", "metadatas"],
        )
        cluster_semantic_by_cluster = _extract_cluster_scores(cluster_result)
        review_semantic_by_cluster = _extract_cluster_scores(review_result)
        return self._rank_cluster_hits(
            query=query,
            cluster_semantic_by_cluster=cluster_semantic_by_cluster,
            review_semantic_by_cluster=review_semantic_by_cluster,
            top_k=top_k,
        )

    def _rank_cluster_hits(
        self,
        *,
        query: str,
        cluster_semantic_by_cluster: dict[str, float],
        review_semantic_by_cluster: dict[str, float],
        top_k: int,
    ) -> list[ClusterHit]:
        """Fuse cluster, review, and lexical signals into ranked hits."""

        keyword_tokens = set(tokenize(query))
        hits: list[ClusterHit] = []
        for cluster in self.clusters:
            cluster_semantic = max(
                0.0, float(cluster_semantic_by_cluster.get(cluster.cluster_id, 0.0))
            )
            review_semantic = max(
                0.0, float(review_semantic_by_cluster.get(cluster.cluster_id, 0.0))
            )
            lexical_score = self._keyword_score(cluster, keyword_tokens)
            fused_score = float(
                (cluster_semantic * 0.55)
                + (review_semantic * 0.25)
                + (lexical_score * 0.20)
            )
            if fused_score <= 0:
                continue
            semantic_signal = cluster_semantic > 0 or review_semantic > 0
            if semantic_signal and lexical_score > 0:
                match_reason = "hybrid"
            elif semantic_signal:
                match_reason = "semantic"
            else:
                match_reason = "keyword"
            hits.append(
                ClusterHit(
                    cluster_id=cluster.cluster_id,
                    score=round(fused_score, 4),
                    match_reason=match_reason,
                    label=cluster.label,
                    summary=cluster.summary,
                    priority_score=cluster.priority_score,
                )
            )
        return sorted(
            hits, key=lambda item: (-item.score, -item.priority_score, item.cluster_id)
        )[: min(top_k, 10)]

    def _keyword_score(self, cluster: ClusterRecord, keyword_tokens: set[str]) -> float:
        """Return normalized keyword overlap for one cluster."""

        if not keyword_tokens:
            return 0.0
        cluster_tokens = set(
            tokenize(f"{cluster.label} {cluster.summary} {' '.join(cluster.keywords)}")
        )
        if not cluster_tokens:
            return 0.0
        overlap = len(keyword_tokens & cluster_tokens)
        denominator = max(1, min(len(keyword_tokens), 5))
        return min(1.0, overlap / denominator)

    def _aggregate_review_scores(self, review_scores: Any) -> dict[str, float]:
        """Collapse review-level semantic scores into cluster-level maxima."""

        aggregated: dict[str, float] = {}
        for index, review in enumerate(self.payload.reviews):
            cluster_id = review.get("cluster_id")
            if not cluster_id:
                continue
            score = float(review_scores[index])
            previous = aggregated.get(cluster_id, 0.0)
            if score > previous:
                aggregated[cluster_id] = score
        return aggregated

    def _transform_query(self, query: str):
        """Vectorize a query with the stored TF-IDF models."""

        word = self.payload.word_vectorizer.transform([query])
        char = self.payload.char_vectorizer.transform([query])
        return hstack([word, char])

    def _transform_query_for_chroma(self, query: str) -> list[float]:
        """Project a query into the dense vector space stored in Chroma."""

        embedding_backend = self.payload.embedding_backend_effective or "projection"
        if embedding_backend != "projection":
            if self.settings is None:
                raise RuntimeError(
                    "External embedding query requested without runtime settings."
                )
            requested_backend = "openai" if embedding_backend == "openai" else "local"
            query_vectors = embed_texts(
                [query],
                self.settings,
                backend_override=requested_backend,
                model_override=self.payload.embedding_model_effective,
                batch_size=1,
            )
            if query_vectors.backend_effective != embedding_backend:
                raise RuntimeError(
                    "Query embedding backend does not match the indexed vector space."
                )
            return np.asarray(query_vectors.vectors[0], dtype=np.float32).tolist()

        combined = self._transform_query(query)
        projector = self.payload.semantic_projector
        normalizer = self.payload.semantic_normalizer
        if projector is not None:
            dense = projector.transform(combined)
            if normalizer is not None:
                dense = normalizer.transform(dense)
        else:
            dense = combined.toarray()
        return np.asarray(dense[0], dtype=np.float32).tolist()

    def _get_chroma_client(self):
        """Lazily initialize the persistent Chroma client."""

        if not self._can_use_chroma():
            raise RuntimeError("Chroma backend is not available for this payload.")
        if self._chroma_client is None:
            self._chroma_client = chromadb.PersistentClient(
                path=self.payload.chroma_path
            )
        return self._chroma_client


def _build_semantic_projection(
    combined_matrix: Any,
) -> tuple[np.ndarray, Any | None, Any | None]:
    """Build compact dense embeddings suitable for Chroma persistence."""

    sample_count, feature_count = combined_matrix.shape
    max_components = min(CHROMA_EMBED_DIM, sample_count - 1, feature_count - 1)
    if max_components >= 2:
        projector = TruncatedSVD(n_components=max_components, random_state=42)
        dense = projector.fit_transform(combined_matrix)
        normalizer = Normalizer(copy=False)
        dense = normalizer.fit_transform(dense)
        return np.asarray(dense, dtype=np.float32), projector, normalizer

    dense = combined_matrix.toarray()
    if dense.size:
        normalizer = Normalizer(copy=False)
        dense = normalizer.fit_transform(dense)
        return np.asarray(dense, dtype=np.float32), None, normalizer
    return np.zeros((sample_count, 1), dtype=np.float32), None, None


def _write_chroma_vectors(
    *,
    session_id: str,
    chroma_path: Path,
    clusters: list[ClusterRecord],
    cluster_docs: list[str],
    cluster_embeddings: np.ndarray,
    reviews: list[dict[str, Any]],
    review_embeddings: np.ndarray,
) -> None:
    """Write session vectors into persistent Chroma collections."""

    client = chromadb.PersistentClient(path=str(chroma_path))
    cluster_collection = client.get_or_create_collection(
        CHROMA_CLUSTER_COLLECTION,
        metadata={"hnsw:space": "cosine"},
    )
    review_collection = client.get_or_create_collection(
        CHROMA_REVIEW_COLLECTION,
        metadata={"hnsw:space": "cosine"},
    )
    cluster_ids = [f"{session_id}:cluster:{cluster.cluster_id}" for cluster in clusters]
    cluster_metadatas = [
        {
            "session_id": session_id,
            "cluster_id": cluster.cluster_id,
            "label": cluster.label,
        }
        for cluster in clusters
    ]
    _upsert_embeddings(
        cluster_collection,
        ids=cluster_ids,
        documents=cluster_docs,
        embeddings=cluster_embeddings,
        metadatas=cluster_metadatas,
    )

    review_ids = [f"{session_id}:review:{review['review_id']}" for review in reviews]
    review_metadatas = [
        {
            "session_id": session_id,
            "review_id": str(review["review_id"]),
            "cluster_id": str(review["cluster_id"]),
            "source": str(review["source"]),
        }
        for review in reviews
    ]
    review_docs = [str(review["text"]) for review in reviews]
    _upsert_embeddings(
        review_collection,
        ids=review_ids,
        documents=review_docs,
        embeddings=review_embeddings,
        metadatas=review_metadatas,
    )


def _upsert_embeddings(
    collection: Any,
    *,
    ids: list[str],
    documents: list[str],
    embeddings: np.ndarray,
    metadatas: list[dict[str, Any]],
) -> None:
    """Upsert dense vectors into Chroma in memory-safe batches."""

    if not ids:
        return
    for start in range(0, len(ids), CHROMA_BATCH_SIZE):
        end = start + CHROMA_BATCH_SIZE
        collection.upsert(
            ids=ids[start:end],
            documents=documents[start:end],
            embeddings=np.asarray(embeddings[start:end], dtype=np.float32).tolist(),
            metadatas=metadatas[start:end],
        )


def _extract_cluster_scores(result: dict[str, Any]) -> dict[str, float]:
    """Convert Chroma query results into cluster-level similarity scores."""

    ids = (result.get("ids") or [[]])[0]
    metadatas = (result.get("metadatas") or [[]])[0]
    distances = (result.get("distances") or [[]])[0]
    scores: dict[str, float] = {}
    for index, _item_id in enumerate(ids):
        metadata = metadatas[index] if index < len(metadatas) else {}
        distance = distances[index] if index < len(distances) else None
        cluster_id = None
        if isinstance(metadata, dict):
            cluster_id = metadata.get("cluster_id")
        if not isinstance(cluster_id, str):
            continue
        similarity = _distance_to_similarity(distance)
        previous = scores.get(cluster_id, 0.0)
        if similarity > previous:
            scores[cluster_id] = similarity
    return scores


def _distance_to_similarity(distance: Any) -> float:
    """Translate Chroma cosine distance into a bounded similarity score."""

    if not isinstance(distance, (float, int)):
        return 0.0
    return max(0.0, min(1.0, 1.0 - float(distance)))


def _hydrate_payload_defaults(payload: RetrievalIndex) -> RetrievalIndex:
    """Backfill optional attributes for indexes created by older versions."""

    if not hasattr(payload, "backend_requested"):
        payload.backend_requested = "local"
    if not hasattr(payload, "backend_effective"):
        payload.backend_effective = "local"
    if not hasattr(payload, "chroma_path"):
        payload.chroma_path = None
    if not hasattr(payload, "cluster_collection_name"):
        payload.cluster_collection_name = None
    if not hasattr(payload, "review_collection_name"):
        payload.review_collection_name = None
    if not hasattr(payload, "embedding_backend_effective"):
        payload.embedding_backend_effective = "projection"
    if not hasattr(payload, "embedding_model_effective"):
        payload.embedding_model_effective = "tfidf-svd-projection"
    if not hasattr(payload, "semantic_projector"):
        payload.semantic_projector = None
    if not hasattr(payload, "semantic_normalizer"):
        payload.semantic_normalizer = None
    return payload
