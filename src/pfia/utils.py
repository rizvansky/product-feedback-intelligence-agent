from __future__ import annotations

import re
import unicodedata
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4


WHITESPACE_RE = re.compile(r"\s+")
TOKEN_RE = re.compile(r"[A-Za-zА-Яа-я0-9]+", re.UNICODE)


def generate_id(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex[:12]}"


def normalize_text(value: str) -> str:
    value = unicodedata.normalize("NFKC", value or "")
    value = value.replace("\u200b", " ")
    value = WHITESPACE_RE.sub(" ", value)
    return value.strip()


def slugify(value: str) -> str:
    normalized = normalize_text(value).lower()
    normalized = re.sub(r"[^a-zа-я0-9]+", "_", normalized, flags=re.IGNORECASE)
    normalized = normalized.strip("_")
    return normalized or "cluster"


def parse_datetime(value: str | None) -> datetime:
    if not value:
        return datetime.now(timezone.utc)
    cleaned = normalize_text(value)
    cleaned = cleaned.replace("Z", "+00:00")
    for candidate in (cleaned, f"{cleaned}T00:00:00+00:00"):
        try:
            parsed = datetime.fromisoformat(candidate)
            if parsed.tzinfo is None:
                return parsed.replace(tzinfo=timezone.utc)
            return parsed.astimezone(timezone.utc)
        except ValueError:
            continue
    for pattern in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d", "%d.%m.%Y", "%d.%m.%Y %H:%M"):
        try:
            parsed = datetime.strptime(cleaned, pattern)
            return parsed.replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return datetime.now(timezone.utc)


def estimate_tokens(text: str) -> int:
    return max(1, len(text) // 4)


def ensure_parent(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)


def tokenize(text: str) -> list[str]:
    return TOKEN_RE.findall(text.lower())
