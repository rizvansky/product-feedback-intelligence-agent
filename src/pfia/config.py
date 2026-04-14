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
    langsmith_tracing: bool = Field(default=False, alias="LANGSMITH_TRACING")
    langsmith_api_key: str = Field(default="", alias="LANGSMITH_API_KEY")
    langsmith_project: str = Field(default="pfia", alias="LANGSMITH_PROJECT")
    langsmith_endpoint: str = Field(
        default="https://api.smith.langchain.com", alias="LANGSMITH_ENDPOINT"
    )
    otel_tracing_enabled: bool = Field(default=False, alias="PFIA_OTEL_TRACING_ENABLED")
    otlp_traces_endpoint: str = Field(
        default="", alias="OTEL_EXPORTER_OTLP_TRACES_ENDPOINT"
    )

    clustering_min_cluster_size: int = 5
    clustering_min_samples: int = 2
    clustering_similarity_threshold: float = 0.03
    clustering_reflection_threshold: float = 0.35
    clustering_reflection_max_profiles: int = 3
    clustering_max_cluster_count: int = 20
    retrieval_top_k: int = 5
    retrieval_backend: str = "chroma"
    orchestrator_backend: str = "langgraph"
    pii_backend: str = "regex+spacy"
    pii_spacy_ru_model: str = "ru_core_news_sm"
    pii_spacy_en_model: str = "en_core_web_sm"
    sentiment_backend: str = "vader"

    embedding_backend: str = "local"
    embedding_primary_model: str = "text-embedding-3-small"
    embedding_fallback_model: str = "paraphrase-multilingual-mpnet-base-v2"
    embedding_batch_size: int = 128
    generation_backend: str = "local"
    llm_primary_model: str = "gpt-4o-mini"
    llm_fallback_model: str = "mistral-small-latest"
    llm_second_fallback_model: str = "claude-3-5-haiku-latest"
    openai_timeout_s: float = 30.0
    openai_max_retries: int = 2
    llm_max_tool_steps: int = 4
    openai_api_key: str = Field(default="", alias="OPENAI_API_KEY")
    openai_base_url: str = Field(
        default="https://api.openai.com/v1", alias="OPENAI_BASE_URL"
    )
    mistral_api_key: str = Field(default="", alias="MISTRAL_API_KEY")
    mistral_base_url: str = Field(
        default="https://api.mistral.ai/v1", alias="MISTRAL_BASE_URL"
    )
    anthropic_api_key: str = Field(default="", alias="ANTHROPIC_API_KEY")
    anthropic_base_url: str = Field(
        default="https://api.anthropic.com/v1", alias="ANTHROPIC_BASE_URL"
    )
    anthropic_api_version: str = Field(
        default="2023-06-01", alias="ANTHROPIC_API_VERSION"
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
    def chroma_persist_dir(self) -> Path:
        """Return the directory for persistent Chroma collections."""
        return self.indexes_dir / "chroma"

    @property
    def reports_dir(self) -> Path:
        """Return the directory for rendered Markdown reports."""
        assert self.data_dir is not None
        return self.data_dir / "reports"

    @property
    def traces_dir(self) -> Path:
        """Return the directory for structured trace artifacts."""
        assert self.data_dir is not None
        return self.artifacts_dir / "traces"

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
            self.chroma_persist_dir,
            self.reports_dir,
            self.raw_dir,
            self.sanitized_dir,
            self.traces_dir,
        ):
            path.mkdir(parents=True, exist_ok=True)

    @property
    def openai_generation_enabled(self) -> bool:
        """Return whether the primary OpenAI provider is enabled."""
        return self.generation_backend == "openai" and bool(self.openai_api_key.strip())

    @property
    def mistral_generation_enabled(self) -> bool:
        """Return whether Mistral fallback generation is enabled."""

        return self.generation_backend == "openai" and bool(
            self.mistral_api_key.strip()
        )

    @property
    def llm_generation_enabled(self) -> bool:
        """Return whether any external LLM provider is enabled."""

        return self.generation_backend == "openai" and (
            bool(self.openai_api_key.strip())
            or bool(self.mistral_api_key.strip())
            or bool(self.anthropic_api_key.strip())
        )

    @property
    def anthropic_generation_enabled(self) -> bool:
        """Return whether Anthropic tertiary fallback generation is enabled."""

        return self.generation_backend == "openai" and bool(
            self.anthropic_api_key.strip()
        )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Load and cache the application settings singleton.

    Returns:
        Initialized settings object with all runtime directories created.
    """
    settings = Settings()
    settings.ensure_directories()
    return settings
