from __future__ import annotations

import pickle
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from scipy.sparse import hstack
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity

from pfia.errors import PFIAError
from pfia.models import (
    ClusterHit,
    ClusterRecord,
    EvidenceBundle,
    QuoteRecord,
    TrendSnippet,
)
from pfia.utils import estimate_tokens, normalize_text, tokenize


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


def build_retrieval_index(
    session_id: str,
    reviews: list[dict[str, Any]],
    clusters: list[ClusterRecord],
    *,
    report_sections: dict[str, str],
    index_path: Path,
) -> None:
    """Build and serialize the retrieval index for one completed session.

    Args:
        session_id: Owning session identifier.
        reviews: Review payload already enriched with cluster assignments.
        clusters: Cluster metadata to index.
        report_sections: Report snippets exposed to Q&A tools.
        index_path: Output file path for the serialized index.
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
    combined = hstack([word_matrix, char_matrix])
    cluster_matrix = combined[: len(cluster_docs)]
    review_matrix = combined[len(cluster_docs) :]
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
    )
    index_path.parent.mkdir(parents=True, exist_ok=True)
    with index_path.open("wb") as handle:
        pickle.dump(payload, handle)


class SessionRetriever:
    """Retrieval helper over a persisted session-scoped index."""

    def __init__(self, payload: RetrievalIndex):
        """Materialize validated cluster models from a retrieval payload.

        Args:
            payload: Deserialized retrieval index.
        """
        self.payload = payload
        self.clusters = [
            ClusterRecord.model_validate(cluster) for cluster in payload.clusters
        ]
        self.cluster_by_id = {cluster.cluster_id: cluster for cluster in self.clusters}

    @classmethod
    def load(cls, index_path: Path) -> "SessionRetriever":
        """Load a retriever from disk.

        Args:
            index_path: Path to the serialized retrieval index.

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
        return cls(payload)

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
        query_matrix = self._transform_query(normalized_query)
        semantic_scores = cosine_similarity(
            query_matrix, self.payload.cluster_matrix
        ).ravel()
        keyword_tokens = set(tokenize(normalized_query))
        hits: list[ClusterHit] = []
        for index, cluster in enumerate(self.clusters):
            keyword_overlap = len(
                keyword_tokens
                & set(
                    tokenize(
                        f"{cluster.label} {cluster.summary} {' '.join(cluster.keywords)}"
                    )
                )
            )
            fused_score = float(
                (semantic_scores[index] * 0.8) + (keyword_overlap * 0.2)
            )
            if fused_score <= 0:
                continue
            if semantic_scores[index] > 0 and keyword_overlap > 0:
                match_reason = "hybrid"
            elif semantic_scores[index] > 0:
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

    def _transform_query(self, query: str):
        """Vectorize a query with the stored TF-IDF models."""
        word = self.payload.word_vectorizer.transform([query])
        char = self.payload.char_vectorizer.transform([query])
        return hstack([word, char])
