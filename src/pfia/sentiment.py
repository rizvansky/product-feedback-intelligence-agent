from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache

from pfia.config import Settings
from pfia.utils import tokenize


POSITIVE_WORDS = {
    "good",
    "great",
    "love",
    "fast",
    "stable",
    "smooth",
    "helpful",
    "excellent",
    "amazing",
    "удобно",
    "отлично",
    "круто",
    "быстро",
    "нравится",
    "полезно",
    "стабильно",
}

NEGATIVE_WORDS = {
    "bad",
    "broken",
    "bug",
    "bugs",
    "crash",
    "crashes",
    "slow",
    "hate",
    "problem",
    "annoying",
    "terrible",
    "ошибка",
    "плохо",
    "медленно",
    "лагает",
    "сломано",
    "вылетает",
    "ужасно",
    "баг",
}


@dataclass(frozen=True)
class SentimentResult:
    """Sentiment score plus the backend that produced it."""

    score: float
    backend_effective: str
    model_effective: str | None = None


@lru_cache(maxsize=1)
def _build_vader_analyzer():
    """Load the optional VADER analyzer once."""

    try:
        from vaderSentiment.vaderSentiment import SentimentIntensityAnalyzer
    except Exception:
        return None
    return SentimentIntensityAnalyzer()


def _lexical_sentiment(text: str) -> float:
    """Score sentiment with the deterministic lexical fallback."""

    tokens = tokenize(text)
    if not tokens:
        return 0.0
    positive = sum(1 for token in tokens if token in POSITIVE_WORDS)
    negative = sum(1 for token in tokens if token in NEGATIVE_WORDS)
    score = (positive - negative) / max(1, len(tokens))
    return float(max(-1.0, min(1.0, score * 4)))


def compute_sentiment(text: str, language: str, settings: Settings) -> SentimentResult:
    """Score review sentiment with VADER first and lexical fallback."""

    lexical_score = _lexical_sentiment(text)
    if settings.sentiment_backend != "vader":
        return SentimentResult(score=lexical_score, backend_effective="lexical")

    analyzer = _build_vader_analyzer()
    if analyzer is None:
        return SentimentResult(score=lexical_score, backend_effective="lexical")

    if language == "ru":
        return SentimentResult(score=lexical_score, backend_effective="lexical")

    vader_score = float(analyzer.polarity_scores(text)["compound"])
    if language == "mixed" and lexical_score != 0.0:
        blended_score = max(-1.0, min(1.0, (vader_score + lexical_score) / 2))
        return SentimentResult(
            score=float(blended_score),
            backend_effective="hybrid",
            model_effective="vaderSentiment",
        )

    return SentimentResult(
        score=vader_score,
        backend_effective="vader",
        model_effective="vaderSentiment",
    )
