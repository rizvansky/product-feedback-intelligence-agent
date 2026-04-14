from __future__ import annotations

from pathlib import Path

from pfia.config import Settings
from pfia.preprocessing import detect_language_chunks, is_spam, preprocess_upload


def test_detect_language_chunks_marks_ru_en_segments():
    """Verify chunk-level language detection splits mixed-language reviews."""

    chunks = detect_language_chunks(
        "После апдейта снова вылетает на оплате. Payment crashes on checkout."
    )

    assert len(chunks) >= 2
    assert {chunk["language"] for chunk in chunks} >= {"ru", "en"}


def test_preprocess_upload_records_mixed_language_chunk_metadata(tmp_path: Path):
    """Verify mixed-language reviews are processed segment by segment."""

    settings = Settings(data_dir=tmp_path / "runtime", _env_file=None)
    upload_path = tmp_path / "mixed.csv"
    upload_path.write_text(
        "review_id,source,text,created_at\n"
        'r1,app_store,"После апдейта снова вылетает на оплате. Payment crashes on checkout.",2026-04-01T00:00:00Z\n',
        encoding="utf-8",
    )

    reviews, summary = preprocess_upload(upload_path, "sess_test", settings)

    assert summary.kept_records == 1
    assert reviews[0].language == "mixed"
    assert reviews[0].metadata["mixed_language_processed"] is True
    assert len(reviews[0].metadata["language_chunks"]) >= 2


def test_spam_heuristic_catches_low_perplexity_repetition():
    """Verify repetitive low-information text is filtered as spam-like."""

    assert is_spam("buy buy buy buy buy buy buy now now now now") is True
