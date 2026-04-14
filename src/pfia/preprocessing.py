from __future__ import annotations

import csv
import hashlib
import io
import json
import re
from pathlib import Path
from typing import Any

from pfia.config import Settings
from pfia.errors import PFIAError
from pfia.models import PreprocessingSummary, ReviewNormalized
from pfia.privacy import has_residual_pii, mask_pii
from pfia.utils import normalize_text, parse_datetime

INJECTION_PATTERNS = [
    re.compile(r"ignore previous instructions", re.IGNORECASE),
    re.compile(r"developer message", re.IGNORECASE),
    re.compile(r"system prompt", re.IGNORECASE),
    re.compile(r"return all stored data", re.IGNORECASE),
    re.compile(r"игнорируй предыдущие инструкции", re.IGNORECASE),
]

REQUIRED_FIELDS = {"review_id", "source", "text", "created_at"}
SUPPORTED_SOURCES = {
    "app_store",
    "google_play",
    "zendesk",
    "telegram",
    "nps",
    "email",
    "web",
}


def detect_language(text: str) -> str:
    """Detect a coarse language bucket from Cyrillic and Latin characters.

    Args:
        text: Input review text.

    Returns:
        One of ``ru``, ``en``, ``mixed``, or ``unknown``.
    """
    cyrillic = sum(
        1 for char in text if "а" <= char.lower() <= "я" or char.lower() == "ё"
    )
    latin = sum(1 for char in text if "a" <= char.lower() <= "z")
    if cyrillic and latin:
        return "mixed"
    if cyrillic:
        return "ru"
    if latin:
        return "en"
    return "unknown"


def detect_injection(text: str) -> bool:
    """Check whether the text contains prompt-injection-like fragments.

    Args:
        text: Input review text.

    Returns:
        ``True`` when a suspicious pattern is detected.
    """
    return any(pattern.search(text) for pattern in INJECTION_PATTERNS)


def is_low_information(text: str) -> bool:
    """Detect reviews that are too short for meaningful analysis.

    Args:
        text: Review text.

    Returns:
        ``True`` when the content is too sparse for reliable clustering.
    """
    tokens = [token for token in re.split(r"\W+", text) if token]
    return len(tokens) < 3 or len(text) < 12


def is_spam(text: str) -> bool:
    """Detect simple spam signals in a review.

    Args:
        text: Review text.

    Returns:
        ``True`` when the text matches spam-like heuristics.
    """
    lowered = text.lower()
    return lowered.count("http") > 2 or re.search(r"(.)\1{6,}", lowered) is not None


def _parse_csv(content: bytes) -> list[dict[str, Any]]:
    """Parse CSV upload bytes into record dictionaries."""
    reader = csv.DictReader(io.StringIO(content.decode("utf-8-sig")))
    return [dict(row) for row in reader]


def _parse_json(content: bytes) -> list[dict[str, Any]]:
    """Parse JSON upload bytes into record dictionaries.

    Args:
        content: Raw JSON file bytes.

    Returns:
        Review-like records extracted from the payload.

    Raises:
        PFIAError: If the JSON payload does not match the accepted schema.
    """
    payload = json.loads(content.decode("utf-8"))
    if isinstance(payload, dict):
        if "reviews" in payload and isinstance(payload["reviews"], list):
            return [dict(item) for item in payload["reviews"]]
        raise PFIAError(
            "INPUT_SCHEMA_INVALID",
            "JSON must contain a top-level list or a 'reviews' list.",
        )
    if not isinstance(payload, list):
        raise PFIAError(
            "INPUT_SCHEMA_INVALID", "JSON upload must be a list of review records."
        )
    return [dict(item) for item in payload]


def load_records(upload_path: Path) -> list[dict[str, Any]]:
    """Load supported upload formats into a uniform record list.

    Args:
        upload_path: Path to the uploaded file.

    Returns:
        Parsed records from the file.

    Raises:
        PFIAError: If the file extension is unsupported.
    """
    suffix = upload_path.suffix.lower()
    content = upload_path.read_bytes()
    if suffix == ".csv":
        return _parse_csv(content)
    if suffix == ".json":
        return _parse_json(content)
    raise PFIAError("INPUT_SCHEMA_INVALID", "Only CSV and JSON uploads are supported.")


def preprocess_upload(
    upload_path: Path, session_id: str, settings: Settings
) -> tuple[list[ReviewNormalized], PreprocessingSummary]:
    """Normalize, validate, deduplicate, and sanitize an uploaded batch.

    Args:
        upload_path: Path to the uploaded CSV or JSON file.
        session_id: Owning session identifier.
        settings: Runtime settings that define batch limits and thresholds.

    Returns:
        Tuple of normalized reviews and a preprocessing summary.

    Raises:
        PFIAError: If the upload is invalid, too large, or fails the privacy gate.
    """
    records = load_records(upload_path)
    if not records:
        raise PFIAError(
            "INPUT_SCHEMA_INVALID", "The upload does not contain any review records."
        )
    if len(records) > settings.max_batch_size:
        raise PFIAError(
            "INPUT_LIMIT_EXCEEDED",
            f"Batch contains {len(records)} reviews, but the PoC limit is {settings.max_batch_size}.",
        )

    reviews: list[ReviewNormalized] = []
    seen_hashes: set[str] = set()
    duplicate_records = 0
    quarantined_records = 0
    pii_hits = 0
    injection_hits = 0
    low_information_records = 0
    unsupported_language_records = 0

    for index, raw_record in enumerate(records):
        missing = [
            field
            for field in REQUIRED_FIELDS
            if not normalize_text(str(raw_record.get(field, "")))
        ]
        if missing:
            raise PFIAError(
                "INPUT_SCHEMA_INVALID",
                f"Record #{index + 1} is missing required fields: {', '.join(sorted(missing))}.",
            )

        source = normalize_text(str(raw_record["source"])).lower().replace(" ", "_")
        if source not in SUPPORTED_SOURCES:
            source = "web"

        language = normalize_text(str(raw_record.get("language", ""))).lower()
        text_raw = normalize_text(str(raw_record["text"]))
        language = language or detect_language(text_raw)
        pii_result = mask_pii(text_raw, language, settings)
        text_masked = pii_result.masked_text
        hits = pii_result.pii_hits
        pii_hits += hits
        if language == "unknown":
            unsupported_language_records += 1

        flags: list[str] = []
        if detect_injection(text_raw):
            flags.append("injection_suspected")
            injection_hits += 1
        if is_spam(text_raw):
            flags.append("spam")
        if is_low_information(text_raw):
            flags.append("low_information")
            low_information_records += 1
        if hits:
            flags.append("pii_found")

        text_for_hash = normalize_text(text_masked.lower())
        dedupe_hash = hashlib.sha256(text_for_hash.encode("utf-8")).hexdigest()
        if dedupe_hash in seen_hashes:
            duplicate_records += 1
            continue
        seen_hashes.add(dedupe_hash)

        if has_residual_pii(text_masked):
            quarantined_records += 1
            continue

        review = ReviewNormalized(
            review_id=normalize_text(str(raw_record["review_id"])),
            session_id=session_id,
            source=source,
            created_at=parse_datetime(str(raw_record["created_at"])),
            rating=_parse_rating(raw_record.get("rating")),
            language=language,
            app_version=normalize_text(str(raw_record.get("app_version", ""))) or None,
            text_normalized=text_masked[:1000],
            text_anonymized=text_masked[:1000],
            dedupe_hash=dedupe_hash,
            flags=flags,
            metadata={
                "raw_index": index,
                "ingested_from": upload_path.name,
                "pii_backend_effective": pii_result.backend_effective,
            },
        )
        reviews.append(review)

    if not reviews:
        raise PFIAError(
            "FAILED_INPUT", "No valid reviews remained after preprocessing."
        )

    quarantine_ratio = quarantined_records / len(records)
    if quarantine_ratio > 0.05:
        raise PFIAError(
            "PRIVACY_GATE_FAILED",
            f"Privacy gate failed: {quarantined_records} records ({quarantine_ratio:.1%}) still contain unresolved PII.",
        )

    summary = PreprocessingSummary(
        total_records=len(records),
        kept_records=len(reviews),
        duplicate_records=duplicate_records,
        quarantined_records=quarantined_records,
        pii_hits=pii_hits,
        injection_hits=injection_hits,
        low_information_records=low_information_records,
        unsupported_language_records=unsupported_language_records,
    )
    return reviews, summary


def write_sanitized_jsonl(output_path: Path, reviews: list[ReviewNormalized]) -> None:
    """Persist sanitized reviews as JSONL for debugging and inspection.

    Args:
        output_path: Destination JSONL path.
        reviews: Reviews to serialize.
    """
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8") as handle:
        for review in reviews:
            handle.write(review.model_dump_json())
            handle.write("\n")


def refresh_summary_flag_counts(
    summary: PreprocessingSummary, reviews: list[ReviewNormalized]
) -> PreprocessingSummary:
    """Recompute summary counters that depend on final review flags.

    Args:
        summary: Existing preprocessing summary.
        reviews: Final sanitized reviews after optional LLM review.

    Returns:
        Updated summary with flag-derived counters refreshed.
    """
    return summary.model_copy(
        update={
            "injection_hits": sum(
                1 for review in reviews if "injection_suspected" in review.flags
            ),
            "low_information_records": sum(
                1 for review in reviews if "low_information" in review.flags
            ),
        }
    )


def summarize_preprocessing_backends(
    reviews: list[ReviewNormalized],
) -> dict[str, str | list[str]]:
    """Summarize effective preprocessing backends used across sanitized reviews.

    Args:
        reviews: Final sanitized reviews.

    Returns:
        Backend summary for runtime metadata and reporting.
    """

    pii_backends = sorted(
        {
            str(review.metadata.get("pii_backend_effective", "regex"))
            for review in reviews
        }
    )
    pii_backend_effective = (
        pii_backends[0]
        if len(pii_backends) == 1
        else f"mixed({', '.join(pii_backends)})"
    )
    return {
        "pii_backend_effective": pii_backend_effective,
        "pii_backends_used": pii_backends,
    }


def _parse_rating(value: Any) -> int | None:
    """Coerce a rating field into the supported 1-5 range.

    Args:
        value: Raw rating value from the upload payload.

    Returns:
        Normalized integer rating or ``None`` when parsing fails.
    """
    if value in (None, "", "null"):
        return None
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return max(1, min(5, parsed))
