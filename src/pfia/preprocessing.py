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
from pfia.utils import normalize_text, parse_datetime


EMAIL_RE = re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.IGNORECASE)
PHONE_RE = re.compile(r"(?:(?:\+|00)\d{1,3}[-\s()]*)?(?:\d[-\s()]*){9,14}\d")
UUID_RE = re.compile(
    r"\b[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}\b",
    re.IGNORECASE,
)
NAME_HINT_RE = re.compile(
    r"\b(?:my name is|i am|i'm|меня зовут|это|зовут)\s+([A-ZА-Я][a-zа-я]{2,20})\b",
    re.IGNORECASE,
)
URL_RE = re.compile(r"https?://\S+", re.IGNORECASE)

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
    return any(pattern.search(text) for pattern in INJECTION_PATTERNS)


def mask_pii(text: str) -> tuple[str, int]:
    pii_hits = 0

    def _sub(pattern: re.Pattern[str], replacement: str, current: str) -> str:
        nonlocal pii_hits
        current, count = pattern.subn(replacement, current)
        pii_hits += count
        return current

    masked = text
    masked = _sub(EMAIL_RE, "[EMAIL]", masked)
    masked = _sub(PHONE_RE, "[PHONE]", masked)
    masked = _sub(UUID_RE, "[DEVICE_ID]", masked)
    masked = _sub(URL_RE, "[URL]", masked)

    def name_repl(match: re.Match[str]) -> str:
        nonlocal pii_hits
        pii_hits += 1
        prefix = match.group(0).split()[0]
        return f"{prefix} [PERSON]"

    masked = NAME_HINT_RE.sub(name_repl, masked)
    return masked, pii_hits


def has_residual_pii(text: str) -> bool:
    return any(pattern.search(text) for pattern in (EMAIL_RE, PHONE_RE, UUID_RE))


def is_low_information(text: str) -> bool:
    tokens = [token for token in re.split(r"\W+", text) if token]
    return len(tokens) < 3 or len(text) < 12


def is_spam(text: str) -> bool:
    lowered = text.lower()
    return lowered.count("http") > 2 or re.search(r"(.)\1{6,}", lowered) is not None


def _parse_csv(content: bytes) -> list[dict[str, Any]]:
    reader = csv.DictReader(io.StringIO(content.decode("utf-8-sig")))
    return [dict(row) for row in reader]


def _parse_json(content: bytes) -> list[dict[str, Any]]:
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

        text_raw = normalize_text(str(raw_record["text"]))
        text_masked, hits = mask_pii(text_raw)
        pii_hits += hits
        language = normalize_text(
            str(raw_record.get("language", ""))
        ).lower() or detect_language(text_raw)
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
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8") as handle:
        for review in reviews:
            handle.write(review.model_dump_json())
            handle.write("\n")


def _parse_rating(value: Any) -> int | None:
    if value in (None, "", "null"):
        return None
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return max(1, min(5, parsed))
