from __future__ import annotations

from pfia.config import Settings
from pfia.privacy import has_residual_pii, mask_pii


def test_regex_masking_replaces_email_phone_and_url():
    """Verify the regex privacy layer masks obvious structured identifiers."""

    settings = Settings(_env_file=None)
    result = mask_pii(
        "Contact me at anna.peterson@example.com or +7 999 123 45 67 https://example.com",
        "en",
        settings,
    )

    assert "[EMAIL]" in result.masked_text
    assert "[PHONE]" in result.masked_text
    assert "[URL]" in result.masked_text
    assert result.pii_hits >= 3
    assert result.backend_effective in {"regex", "regex+spacy"}


def test_spacy_person_masking_is_used_when_model_is_available(monkeypatch):
    """Verify spaCy augments regex masking with person-entity replacement."""

    settings = Settings(_env_file=None)

    class FakeEntity:
        """Minimal spaCy-like entity stub."""

        def __init__(self, start_char: int, end_char: int, label_: str, text: str):
            self.start_char = start_char
            self.end_char = end_char
            self.label_ = label_
            self.text = text

    class FakeDoc:
        """Minimal spaCy-like doc stub."""

        def __init__(self, text: str):
            start = text.index("John Smith")
            end = start + len("John Smith")
            self.ents = [FakeEntity(start, end, "PERSON", "John Smith")]

    class FakeNlp:
        """Minimal callable spaCy-like pipeline stub."""

        def __call__(self, text: str):
            return FakeDoc(text)

    monkeypatch.setattr("pfia.privacy._load_spacy_model", lambda _name: FakeNlp())

    result = mask_pii("John Smith said payment keeps crashing.", "en", settings)

    assert "[PERSON]" in result.masked_text
    assert result.backend_effective == "regex+spacy"
    assert result.pii_hits >= 1


def test_residual_pii_only_tracks_high_confidence_regex_patterns():
    """Verify the residual gate stays conservative after masking."""

    assert has_residual_pii("email me at anna.peterson@example.com") is True
    assert has_residual_pii("Thanks [PERSON] from support for helping.") is False
