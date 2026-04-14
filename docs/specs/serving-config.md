# Serving & Config

## Назначение

Документ задаёт операционную конфигурацию PoC: как сервисы поднимаются, какие переменные обязательны и какие resource limits считаются валидными.

## Контейнеры

Локальный PoC-профиль использует четыре runtime-сервиса:

| Контейнер | Роль |
|---|---|
| `frontend` | Next.js 14 UI + same-origin proxy до backend |
| `api` | FastAPI serving layer + built-in UI |
| `worker` | orchestrator и batch-processing |
| `chroma` | отдельный vector store service для retrieval и session-scoped indexes |

Дополнительно, без отдельного контейнера:

- SQLite на общем volume;
- local storage volume для upload-файлов, sanitized artifacts и reports;
- session pickle fallback рядом с Chroma collections для report sections, trends и lexical retrieval fallback.

Hosted deploy profile для Railway имеет два варианта:

- canonical full profile:
  - `frontend` как отдельный public service;
  - `api` как отдельный private/public service;
  - `chroma` как отдельный private service;
  - embedded worker внутри `api`;
  - volumes у `api` и `chroma`.
- fallback profile:
  - один `api` web-service;
  - embedded worker внутри того же процесса;
  - runtime state уходит в Railway volume через `RAILWAY_VOLUME_MOUNT_PATH`;
  - built-in FastAPI UI остаётся доступным.

Production `api` image по умолчанию устанавливает `en_core_web_sm` и `ru_core_news_sm`, но **не** ставит local `sentence-transformers` fallback, чтобы Railway build не раздувался из-за `torch`/CUDA stack.

Текущий проверенный hosted deployment использует:

- `frontend` как основной user-facing URL;
- `api` как отдельный smoke/health endpoint;
- `chroma` как отдельный HTTP backend для retrieval;
- `PFIA_CHROMA_MODE=http` и рабочий `chroma_endpoint_effective` в runtime metadata.

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
| `PFIA_CHROMA_MODE` | `embedded` / `http` |
| `PFIA_CHROMA_HOST` | host внешнего Chroma service |
| `PFIA_CHROMA_PORT` | port внешнего Chroma service |
| `PFIA_CHROMA_SSL` | включает TLS для Chroma HTTP client |
| `PFIA_MAX_BATCH_SIZE` | верхняя граница batch |
| `PFIA_REPORT_TOP_CLUSTERS` | число top themes в report/UI |
| `PFIA_REPORT_QUOTES_PER_CLUSTER` | число quote excerpts на тему в report |
| `PFIA_ALERT_QUOTES_PER_CLUSTER` | число quote excerpts в alert section |
| `PFIA_LOW_DATA_REVIEW_THRESHOLD` | порог simple-list mode для batch'ей < N отзывов |
| `PFIA_WEAK_SIGNAL_MAX_CLUSTER_SIZE` | максимум размера weak-signal cluster |
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

- `presentation_mode`;
- `low_data_mode`;
- `trace_correlation_id`;
- `trace_exporters_effective`;
- `trace_local_path`.
- `weak_signal_cluster_ids`;
- `mixed_sentiment_cluster_ids`;
- `mixed_language_review_count`;
- `chroma_mode_effective`;
- `chroma_endpoint_effective`.

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

Если нужен canonical hosted full profile, добавляются ещё сервисы:

| Компонент | CPU / RAM target |
|---|---|
| `pfia-frontend` | от 0,5 vCPU / 512 MB |
| `pfia-chroma` | от 0,5 vCPU / 1 GB |

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
| `PFIA_INSTALL_LOCAL_EMBEDDINGS` | включает optional install `sentence-transformers` в `api/worker` image |

Политика по умолчанию:

- Railway / production image: `true`;
- local `docker compose`: `false`;
- если нужен full privacy path локально в Docker, включай build arg вручную.

Для `PFIA_INSTALL_LOCAL_EMBEDDINGS`:

- Railway / production image: `false` по умолчанию;
- local full-offline Docker: `true`, только если нужен именно local embeddings fallback;
- если включить этот arg в Railway, image заметно вырастет.

## Frontend Proxy Config

Отдельный Next.js frontend использует два env-параметра:

| Переменная | Назначение |
|---|---|
| `NEXT_PUBLIC_PFIA_API_BASE_URL` | browser-visible base path; по умолчанию `/pfia` |
| `PFIA_INTERNAL_API_BASE_URL` | internal rewrite target для Next.js server; локально `http://127.0.0.1:8000`, в compose `http://api:8000` |

Такой proxy-контур позволяет держать browser traffic same-origin и не включать CORS в FastAPI только ради frontend-сервиса.

Для `PFIA_CHROMA_*` в hosted Railway deployment допустимы два рабочих варианта:

- private networking:
  - `PFIA_CHROMA_HOST=chroma.railway.internal`
  - `PFIA_CHROMA_PORT=8000`
  - `PFIA_CHROMA_SSL=false`
- public TLS endpoint:
  - `PFIA_CHROMA_HOST=chroma-production-4408.up.railway.app`
  - `PFIA_CHROMA_PORT=443`
  - `PFIA_CHROMA_SSL=true`

Текущий публично проверенный deployment использует второй вариант.
