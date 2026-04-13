from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import AliasChoices, Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime configuration for the PFIA application."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_prefix="PFIA_",
        extra="ignore",
        populate_by_name=True,
    )

    app_name: str = "PFIA"
    env: str = "dev"
    data_dir: Path | None = None
    railway_volume_mount_path: Path | None = Field(
        default=None, alias="RAILWAY_VOLUME_MOUNT_PATH"
    )
    host: str = "0.0.0.0"
    port: int = Field(default=8000, validation_alias=AliasChoices("PFIA_PORT", "PORT"))
    log_level: str = "INFO"
    worker_poll_interval_s: float = 1.0
    worker_heartbeat_ttl_s: int = 20
    embedded_worker: bool | None = None

    max_batch_size: int = 2000
    max_upload_size_bytes: int = 10 * 1024 * 1024
    max_queue_depth: int = 3
    report_top_clusters: int = 10
    session_retention_days: int = 7

    clustering_min_cluster_size: int = 5
    clustering_min_samples: int = 2
    clustering_similarity_threshold: float = 0.03
    retrieval_top_k: int = 5

    embedding_backend: str = "local"
    generation_backend: str = "local"
    llm_primary_model: str = "gpt-4o-mini"
    llm_fallback_model: str = "local-template"
    openai_timeout_s: float = 30.0
    openai_max_retries: int = 2
    llm_max_tool_steps: int = 4
    openai_api_key: str = Field(default="", alias="OPENAI_API_KEY")
    openai_base_url: str = Field(
        default="https://api.openai.com/v1", alias="OPENAI_BASE_URL"
    )

    prometheus_enabled: bool = True

    @model_validator(mode="after")
    def apply_hosting_defaults(self) -> Settings:
        """Derive hosting-specific defaults after environment loading.

        Returns:
            The mutated settings object with resolved data directory and worker mode.
        """
        if self.data_dir is None:
            if self.railway_volume_mount_path is not None:
                self.data_dir = self.railway_volume_mount_path / "runtime"
            else:
                self.data_dir = Path("data/runtime")
        if self.embedded_worker is None:
            self.embedded_worker = self.railway_volume_mount_path is not None
        return self

    @property
    def db_path(self) -> Path:
        """Return the SQLite database path."""
        assert self.data_dir is not None
        return self.data_dir / "pfia.sqlite3"

    @property
    def uploads_dir(self) -> Path:
        """Return the directory for uploaded source files."""
        assert self.data_dir is not None
        return self.data_dir / "uploads"

    @property
    def artifacts_dir(self) -> Path:
        """Return the directory for generated intermediate artifacts."""
        assert self.data_dir is not None
        return self.data_dir / "artifacts"

    @property
    def indexes_dir(self) -> Path:
        """Return the directory for persisted retrieval indexes."""
        assert self.data_dir is not None
        return self.data_dir / "indexes"

    @property
    def reports_dir(self) -> Path:
        """Return the directory for rendered Markdown reports."""
        assert self.data_dir is not None
        return self.data_dir / "reports"

    @property
    def raw_dir(self) -> Path:
        """Return the directory for raw uploaded files."""
        return self.uploads_dir / "raw"

    @property
    def sanitized_dir(self) -> Path:
        """Return the directory for sanitized preprocessing artifacts."""
        return self.artifacts_dir / "sanitized"

    def ensure_directories(self) -> None:
        """Create the runtime directory tree required by the application."""
        assert self.data_dir is not None
        for path in (
            self.data_dir,
            self.uploads_dir,
            self.artifacts_dir,
            self.indexes_dir,
            self.reports_dir,
            self.raw_dir,
            self.sanitized_dir,
        ):
            path.mkdir(parents=True, exist_ok=True)

    @property
    def openai_generation_enabled(self) -> bool:
        """Return whether OpenAI-backed agent generation is enabled."""
        return self.generation_backend == "openai" and bool(self.openai_api_key.strip())


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Load and cache the application settings singleton.

    Returns:
        Initialized settings object with all runtime directories created.
    """
    settings = Settings()
    settings.ensure_directories()
    return settings
