from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache
from typing import Any

from pfia.config import Settings


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
PERSON_ENTITY_LABELS = {"PERSON", "PER"}


@dataclass(frozen=True)
class PiiMaskingResult:
    """Masked text plus diagnostics about the PII pipeline."""

    masked_text: str
    pii_hits: int
    backend_effective: str


@lru_cache(maxsize=1)
def _spacy_module() -> Any | None:
    """Load the optional spaCy module once."""

    try:
        import spacy
    except Exception:
        return None
    return spacy


@lru_cache(maxsize=8)
def _load_spacy_model(model_name: str) -> Any | None:
    """Load one spaCy model if it is installed locally."""

    spacy = _spacy_module()
    if spacy is None:
        return None
    try:
        return spacy.load(
            model_name,
            disable=[
                "attribute_ruler",
                "lemmatizer",
                "morphologizer",
                "parser",
                "tagger",
                "textcat",
            ],
        )
    except Exception:
        return None


def _resolve_spacy_models(language: str, settings: Settings) -> list[str]:
    """Map a coarse language bucket to configured spaCy NER models."""

    if settings.pii_backend != "regex+spacy":
        return []
    if language == "ru":
        return [settings.pii_spacy_ru_model]
    if language == "en":
        return [settings.pii_spacy_en_model]
    if language == "mixed":
        return [settings.pii_spacy_en_model, settings.pii_spacy_ru_model]
    return []


def _mask_with_regex(text: str) -> tuple[str, int]:
    """Apply high-confidence regex masking to obvious PII."""

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


def _mask_person_entities(text: str, model_names: list[str]) -> tuple[str, int, bool]:
    """Mask person entities with any available spaCy NER model."""

    masked = text
    total_hits = 0
    spacy_used = False

    for model_name in model_names:
        nlp = _load_spacy_model(model_name)
        if nlp is None:
            continue
        spacy_used = True
        doc = nlp(masked)
        replacements: list[tuple[int, int]] = []
        for entity in doc.ents:
            if entity.label_ not in PERSON_ENTITY_LABELS:
                continue
            entity_text = entity.text.strip()
            if len(entity_text) < 3:
                continue
            if any(char.isdigit() for char in entity_text):
                continue
            if entity_text.startswith("[") and entity_text.endswith("]"):
                continue
            replacements.append((entity.start_char, entity.end_char))
        last_start = len(masked) + 1
        for start, end in sorted(replacements, key=lambda item: item[0], reverse=True):
            if end > last_start:
                continue
            masked = masked[:start] + "[PERSON]" + masked[end:]
            total_hits += 1
            last_start = start
    return masked, total_hits, spacy_used


def mask_pii(text: str, language: str, settings: Settings) -> PiiMaskingResult:
    """Mask obvious PII with regex and optional spaCy NER."""

    masked_text, pii_hits = _mask_with_regex(text)
    spacy_models = _resolve_spacy_models(language, settings)
    masked_text, spacy_hits, spacy_used = _mask_person_entities(
        masked_text, spacy_models
    )
    return PiiMaskingResult(
        masked_text=masked_text,
        pii_hits=pii_hits + spacy_hits,
        backend_effective="regex+spacy" if spacy_used else "regex",
    )


def has_residual_pii(text: str) -> bool:
    """Check whether masked text still contains unresolved regex PII."""

    return any(pattern.search(text) for pattern in (EMAIL_RE, PHONE_RE, UUID_RE))
