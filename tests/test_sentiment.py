from __future__ import annotations

from pfia.config import Settings
from pfia.sentiment import SentimentResult, compute_sentiment


def test_sentiment_uses_lexical_fallback_when_vader_is_unavailable(monkeypatch):
    """Verify the lexical scorer remains the safe baseline without VADER."""

    monkeypatch.setattr("pfia.sentiment._build_vader_analyzer", lambda: None)
    settings = Settings(_env_file=None)

    result = compute_sentiment(
        "Terrible crash and broken checkout flow.", "en", settings
    )

    assert isinstance(result, SentimentResult)
    assert result.backend_effective == "lexical"
    assert result.score < 0


def test_sentiment_uses_vader_for_english_when_available(monkeypatch):
    """Verify VADER becomes the primary backend for English text."""

    class FakeAnalyzer:
        """Minimal VADER-like analyzer stub."""

        def polarity_scores(self, _text: str) -> dict[str, float]:
            return {"compound": 0.64}

    monkeypatch.setattr("pfia.sentiment._build_vader_analyzer", lambda: FakeAnalyzer())
    settings = Settings(_env_file=None)

    result = compute_sentiment("Great stable release, love the update.", "en", settings)

    assert result.backend_effective == "vader"
    assert result.model_effective == "vaderSentiment"
    assert result.score == 0.64


def test_sentiment_blends_vader_and_lexical_for_mixed_text(monkeypatch):
    """Verify mixed-language text uses the hybrid path when both signals exist."""

    class FakeAnalyzer:
        """Minimal VADER-like analyzer stub."""

        def polarity_scores(self, _text: str) -> dict[str, float]:
            return {"compound": -0.4}

    monkeypatch.setattr("pfia.sentiment._build_vader_analyzer", lambda: FakeAnalyzer())
    settings = Settings(_env_file=None)

    result = compute_sentiment(
        "Приложение bad и вылетает on checkout.", "mixed", settings
    )

    assert result.backend_effective == "hybrid"
    assert result.model_effective == "vaderSentiment"
    assert -1.0 <= result.score <= 1.0
