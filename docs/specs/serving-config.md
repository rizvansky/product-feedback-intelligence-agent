# Serving & Config

## Назначение

Документ задаёт операционную конфигурацию PoC: как сервисы поднимаются, какие переменные обязательны и какие resource limits считаются валидными.

## Контейнеры

Локальный PoC-профиль использует два runtime-сервиса:

| Контейнер | Роль |
|---|---|
| `api` | FastAPI serving layer + встроенный статический frontend |
| `worker` | orchestrator и batch-processing |

Дополнительно, без отдельного контейнера:

- SQLite на общем volume;
- local storage volume для upload-файлов, sanitized artifacts, reports и persistent Chroma storage;
- session pickle fallback рядом с Chroma collections для report sections, trends и lexical retrieval fallback.

Hosted deploy profile для Railway упрощён до одного web-service:

- `api` остаётся FastAPI serving layer;
- `worker` поднимается как embedded background thread внутри того же процесса;
- runtime state уходит в Railway volume через `RAILWAY_VOLUME_MOUNT_PATH`.
- production image по умолчанию устанавливает `en_core_web_sm` и `ru_core_news_sm`.

## Обязательные конфиги

| Переменная | Назначение |
|---|---|
| `PFIA_ENV` | `dev` / `demo` / `prod` |
| `PFIA_DATA_DIR` | корневая директория данных |
| `PFIA_EMBEDDED_WORKER` | принудительно включает single-service mode |
| `PFIA_PORT` / `PORT` | HTTP port |
| `LANGSMITH_TRACING` | включает optional LangSmith sink |
| `LANGSMITH_API_KEY` | ключ LangSmith |
| `LANGSMITH_PROJECT` | project name для LangSmith |
| `LANGSMITH_ENDPOINT` | endpoint LangSmith API |
| `PFIA_OTEL_TRACING_ENABLED` | включает optional OTLP sink |
| `OTEL_EXPORTER_OTLP_TRACES_ENDPOINT` | OTLP traces endpoint |
| `PFIA_ORCHESTRATOR_BACKEND` | `langgraph` / `linear` |
| `PFIA_PII_BACKEND` | `regex+spacy` / `regex` |
| `PFIA_PII_SPACY_RU_MODEL` | spaCy NER model for Russian PII masking |
| `PFIA_PII_SPACY_EN_MODEL` | spaCy NER model for English PII masking |
| `PFIA_SENTIMENT_BACKEND` | `vader` / `lexical` |
| `PFIA_CLUSTERING_MIN_CLUSTER_SIZE` | базовый `min_cluster_size` для HDBSCAN |
| `PFIA_CLUSTERING_MIN_SAMPLES` | базовый `min_samples` для HDBSCAN |
| `PFIA_CLUSTERING_SIMILARITY_THRESHOLD` | порог degraded-mode для cluster quality |
| `PFIA_CLUSTERING_REFLECTION_THRESHOLD` | quality gate для повторных HDBSCAN профилей |
| `PFIA_CLUSTERING_REFLECTION_MAX_PROFILES` | максимум HDBSCAN профилей на один batch |
| `PFIA_CLUSTERING_MAX_CLUSTER_COUNT` | верхняя граница cluster-count gate |
| `PFIA_EMBEDDING_BACKEND` | `openai` / `local` |
| `PFIA_EMBEDDING_PRIMARY_MODEL` | primary embedding model |
| `PFIA_EMBEDDING_FALLBACK_MODEL` | local embedding fallback model |
| `PFIA_EMBEDDING_BATCH_SIZE` | размер батча для embedding calls |
| `OPENAI_API_KEY` | ключ primary provider OpenAI |
| `OPENAI_BASE_URL` | base URL внешнего provider |
| `MISTRAL_API_KEY` | ключ fallback provider Mistral |
| `MISTRAL_BASE_URL` | base URL fallback provider |
| `ANTHROPIC_API_KEY` | ключ fallback provider Anthropic |
| `ANTHROPIC_BASE_URL` | base URL fallback provider Anthropic |
| `ANTHROPIC_API_VERSION` | версия Anthropic Messages API |
| `PFIA_LLM_PRIMARY_MODEL` | основная generation model |
| `PFIA_LLM_FALLBACK_MODEL` | резервная generation model |
| `PFIA_LLM_SECOND_FALLBACK_MODEL` | резервная generation model fallback 2 |
| `PFIA_RETRIEVAL_BACKEND` | `chroma` / `local` |
| `PFIA_MAX_BATCH_SIZE` | верхняя граница batch |
| `PFIA_SESSION_RETENTION_DAYS` | срок хранения артефактов |
| `PFIA_PROMETHEUS_ENABLED` | включение `/metrics` |

## Версии моделей

Для каждой session конфигурация моделей фиксируется snapshot'ом:

- `generation_primary`;
- `generation_fallback`;
- `generation_second_fallback`;
- `embedding_backend`;
- `embedding_primary`;
- `embedding_fallback`;
- `pii_backend`;
- `sentiment_backend`;
- `clustering_profile`.

Это нужно, чтобы результат можно было объяснить и воспроизвести.

Дополнительно runtime фиксирует:

- `trace_correlation_id`;
- `trace_exporters_effective`;
- `trace_local_path`.

## Health endpoints

| Endpoint | Назначение |
|---|---|
| `/health/live` | процесс жив |
| `/health/ready` | зависимости готовы принимать трафик |
| `/metrics` | Prometheus-compatible metrics |

`ready` считается `false`, если:

- нет доступа к SQLite или data volume;
- worker не может обновлять heartbeat;
- heartbeat от worker протух дольше заданного окна.

## Ресурсные ограничения

| Компонент | CPU / RAM target |
|---|---|
| `api` | до 0,5 vCPU / 512 MB |
| `worker` | до 1 vCPU / 2 GB |

Для Railway-hosted single-service режима целевой sizing проще:

| Компонент | CPU / RAM target |
|---|---|
| `pfia-web` | от 1 vCPU / 1 GB |

## Секреты

- Ключи не коммитятся в репозиторий.
- `.env` используется только локально.
- В логах и trace metadata ключи маскируются.

## Операционные правила

- Один batch-job одновременно.
- Новые jobs не принимаются в `RUNNING`, если очередь уже заполнена.
- Chat доступен только для завершённых session.
- Hosted deploy фиксируется в `1` реплику, пока state основан на SQLite и локальных файлах.

## Build-time switches

| Build arg | Назначение |
|---|---|
| `PFIA_INSTALL_SPACY_MODELS` | включает установку `en_core_web_sm` и `ru_core_news_sm` во время `docker build` |

Политика по умолчанию:

- Railway / production image: `true`;
- local `docker compose`: `false`;
- если нужен full privacy path локально в Docker, включай build arg вручную.
