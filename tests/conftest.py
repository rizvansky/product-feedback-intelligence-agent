from __future__ import annotations

from pathlib import Path

import pytest

from pfia.api import create_app
from pfia.config import Settings


@pytest.fixture()
def demo_file_path() -> Path:
    """Return the path to the baked-in demo dataset fixture."""
    return (
        Path(__file__).resolve().parents[1] / "data" / "demo" / "mobile_app_reviews.csv"
    )


@pytest.fixture()
def app(tmp_path: Path):
    """Create an isolated FastAPI app backed by a temporary runtime directory."""
    settings = Settings(
        data_dir=tmp_path / "runtime",
        generation_backend="local",
        embedding_backend="local",
        openai_api_key="",
        _env_file=None,
    )
    return create_app(settings)
