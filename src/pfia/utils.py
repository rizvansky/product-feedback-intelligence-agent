from __future__ import annotations

import re
import unicodedata
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4


WHITESPACE_RE = re.compile(r"\s+")
TOKEN_RE = re.compile(r"[A-Za-zА-Яа-я0-9]+", re.UNICODE)


def generate_id(prefix: str) -> str:
    """Generate a short stable-looking identifier with the given prefix.

    Args:
        prefix: Logical namespace for the identifier, such as ``sess`` or ``job``.

    Returns:
        A prefixed random identifier suitable for URLs and logs.
    """
    return f"{prefix}_{uuid4().hex[:12]}"


def normalize_text(value: str) -> str:
    """Normalize free-form text for downstream deterministic processing.

    Args:
        value: Raw text input.

    Returns:
        Trimmed text with normalized Unicode and collapsed whitespace.
    """
    value = unicodedata.normalize("NFKC", value or "")
    value = value.replace("\u200b", " ")
    value = WHITESPACE_RE.sub(" ", value)
    return value.strip()


def slugify(value: str) -> str:
    """Convert free-form text into a storage-friendly slug.

    Args:
        value: Source label or phrase.

    Returns:
        Lowercased slug using underscores as separators.
    """
    normalized = normalize_text(value).lower()
    normalized = re.sub(r"[^a-zа-я0-9]+", "_", normalized, flags=re.IGNORECASE)
    normalized = normalized.strip("_")
    return normalized or "cluster"


def parse_datetime(value: str | None) -> datetime:
    """Parse common date formats into a UTC-aware timestamp.

    Args:
        value: Incoming date string or ``None``.

    Returns:
        Parsed UTC datetime. Falls back to ``now()`` when parsing fails.
    """
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
    """Estimate token count using a coarse character-based heuristic.

    Args:
        text: Text fragment whose prompt size should be approximated.

    Returns:
        Approximate token count.
    """
    return max(1, len(text) // 4)


def ensure_parent(path: Path) -> None:
    """Create the parent directory for a file path if it does not exist.

    Args:
        path: Target file path.
    """
    path.parent.mkdir(parents=True, exist_ok=True)


def tokenize(text: str) -> list[str]:
    """Extract lowercase alphanumeric tokens from multilingual text.

    Args:
        text: Input text.

    Returns:
        Token list used by simple lexical heuristics.
    """
    return TOKEN_RE.findall(text.lower())
