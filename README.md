# Product Feedback Intelligence Agent (PFIA)

PFIA - это PoC-система для пакетного анализа пользовательского фидбека: загрузка CSV/JSON, анонимизация PII, тематическая кластеризация, приоритизация проблем, anomaly detection, Markdown-отчёт и grounded Q&A по уже обработанной сессии.

Репозиторий содержит рабочий PoC, который запускается локально без внешних API-ключей. По умолчанию локальный demo использует offline-профиль: локальная аналитика, SQLite, persistent Chroma-backed retrieval с projection fallback и отдельный `Next.js` frontend с proxy-контуром до FastAPI API. При наличии `OPENAI_API_KEY` включается proposal-grade embedding path (`text-embedding-3-small`) и LLM-backed multi-agent runtime. Local `sentence-transformers` fallback остаётся доступным, но в production Docker image по умолчанию не ставится, чтобы Railway build не раздувался из-за `torch`/CUDA stack.

## Live Demo

Текущий публичный деплой, проверенный 14 апреля 2026 года:

- Main UI URL: `https://frontend-production-c4b0.up.railway.app`
- API URL: `https://api-production-242f.up.railway.app`
- Chroma diagnostics URL: `https://chroma-production-4408.up.railway.app/api/v2/heartbeat`
- GitHub repository: `https://github.com/rizvansky/product-feedback-intelligence-agent`

Быстрая проверка hosted PoC занимает 1-2 минуты:

1. Открыть main UI URL.
1. Нажать `Run Demo Dataset`.
1. Дождаться статуса `COMPLETED`.
1. Убедиться, что в UI появились clusters, Markdown report, timeline events и runtime metadata.
1. В Q&A задать: `What is the highest-priority issue and what evidence supports it?`

Ожидаемый результат:

- top issue: `Payment flow crashes`
- количество top clusters в presentation mode: `5`
- weak signals отображаются отдельно
- health endpoints доступны на API URL:
  - `/health/live`
  - `/health/ready`
  - `/metrics`

## Что реализовано

- Upload отзывов в формате CSV/JSON через web UI и HTTP API.
- Асинхронный batch-flow через `job` + отдельный worker process.
- Privacy gate:
  - masking email / phone / device-like identifiers;
  - quarantine при residual PII;
  - raw PII не сохраняется в sanitized artifacts, отчётах и retrieval.
- Локальный analysis pipeline:
  - multilingual preprocessing;
  - chunk-level RU/EN handling for mixed-language reviews;
  - concept-enhanced clustering;
  - priority scoring;
  - anomaly detection по weekly baseline;
  - Markdown report + executive summary + top quotes per theme;
  - low-data mode with simple-list-first presentation;
  - weak-signal separation for 2-3 review clusters;
  - mixed-sentiment cluster marking in summary/runtime diagnostics.
- Опциональный OpenAI multi-agent слой:
  - `PreprocessReviewAgent` для second-pass review пограничных `spam` / `injection_suspected` / `low_information` флагов;
  - `ClusterReviewAgent` для merge/split review поверх детерминированной кластеризации;
  - `TaxonomyAgent` для label/summary refinement по анонимизированным примерам;
  - `AnomalyExplainerAgent` для PM-friendly anomaly explanations;
  - `ExecutiveSummaryAgent` для executive summary;
  - `QueryPlannerAgent` + `AnswerWriterAgent` для tool-using grounded Q&A.
- Grounded Q&A с tool-like retrieval:
  - `top_clusters`;
  - `search_clusters`;
  - `get_quotes`;
  - `get_trend`;
  - `compare_clusters`;
  - `get_report_section`.
- Runtime metadata для каждого completed run:
  - effective orchestrator backend;
  - presentation mode and low-data flag;
  - trace correlation id;
  - effective backend;
  - effective PII backend;
  - effective sentiment backend;
  - effective retrieval backend;
  - provider call counts and token totals;
  - input filename;
  - records kept;
  - top cluster ids;
  - weak-signal cluster ids;
  - mixed-sentiment cluster ids;
  - mixed-language review count;
  - per-agent usage snapshot.
- Health endpoints, Prometheus-compatible `/metrics`, Docker Compose, demo dataset и тесты.
- Hosted deploy profile под Railway:
  - canonical full profile: `frontend + api + chroma`;
  - operational fallback profile: `api` only с embedded worker и built-in UI.

## Реальный stack в репозитории

Репозиторий поддерживает два рабочих deployment/runtime профиля:

- `compose / multi-service`: отдельные `frontend`, `api`, `worker`, `chroma`.
- `railway / full profile`: отдельные `frontend`, `api`, `chroma`, где worker встроен в `api`.
- `hosted / fallback profile`: только `api` + embedded worker + persistent volume.

Состав сервисов и модульные границы при этом остаются одинаковыми:

- `frontend`: отдельный `Next.js 14` App Router UI с `shadcn/ui`-style component layer и same-origin proxy к backend;
- `api`: FastAPI-приложение, которое отдаёт HTTP API и встроенный UI;
- `worker`: отдельный Python worker для очереди jobs;
- `storage`: SQLite + локальные файлы артефактов + `Chroma` vector store + session pickle fallback для retrieval.

Для Railway в репозитории подготовлены service-specific configs:

- `railway.json` в корне для `api`;
- `frontend/railway.json` для `frontend`;
- `chroma/railway.json` для отдельного `chroma` service.

Канонический Railway-профиль для наиболее полной реализации proposal:

- `frontend`: публичный `Next.js` service;
- `api`: FastAPI + embedded worker + volume `/data`;
- `chroma`: отдельный Chroma HTTP-service + volume `/data`.

Fallback single-service профиль остаётся поддерживаемым, но рассматривается как упрощённый operational mode, а не как основной proposal-aligned deploy.

## Быстрый старт

### Вариант 1. Docker Compose

Требования:

- Docker
- Docker Compose

Запуск:

```bash
docker compose up --build
```

После старта открой:

- Next.js UI: `http://localhost:3000`
- Built-in FastAPI UI: `http://localhost:8000`
- live health: `http://localhost:8000/health/live`
- ready health: `http://localhost:8000/health/ready`
- metrics: `http://localhost:8000/metrics`

`docker-compose.yml` использует `.env.example` с безопасными offline-дефолтами, так что для demo дополнительная настройка не нужна.

В compose-профиле:

- `frontend` работает как отдельный сервис и ходит в backend через Next.js rewrite proxy `/pfia -> http://api:8000`, поэтому browser-side запросы не упираются в CORS;
- `chroma` поднят как отдельный service и обслуживает retrieval/indexing по `http://chroma:8001`;
- `api` и `worker` подключаются к нему через `PFIA_CHROMA_MODE=http`.

Примечание: внутри Docker data dir принудительно переопределяется в `/app/data/runtime`, поэтому локальный host-path и контейнерный path не конфликтуют.
Примечание: local `docker compose` по умолчанию собирает `api/worker` image без `spaCy`-моделей и без local `sentence-transformers`, чтобы сборка оставалась легче и быстрее.

Для максимально полного local Docker path:

```bash
PFIA_INSTALL_SPACY_MODELS=true PFIA_INSTALL_LOCAL_EMBEDDINGS=true docker compose build
docker compose up -d
```

Остановка:

```bash
docker compose down -v
```

### Вариант 2. Локальный запуск без Docker

Требования:

- Python 3.10+
- виртуальное окружение

Установка зависимостей:

```bash
make install
```

Это ставит:

- dev tooling;
- local `sentence-transformers` fallback для embeddings.

Для облегчённого локального runtime без `sentence-transformers`:

```bash
make install-minimal
```

Запуск API:

```bash
PYTHONPATH=src ./.venv/bin/python -m pfia.api
```

Запуск worker в отдельном терминале:

```bash
PYTHONPATH=src ./.venv/bin/python -m pfia.worker
```

Установка зависимостей frontend:

```bash
make frontend-install
```

Запуск Next.js frontend:

```bash
make run-frontend
```

После этого открой:

- Next.js UI: `http://localhost:3000`
- Built-in FastAPI UI: `http://localhost:8000`

Локальная симуляция hosted-профиля:

```bash
make run-embedded
```

`Makefile` для локальных команд принудительно использует `PFIA_DATA_DIR=data/runtime`, даже если в `.env` остались контейнерные настройки.

Тесты:

```bash
./.venv/bin/python -m pytest -q
```

Есть `Makefile`:

```bash
make test
make evals
make demo
make run-frontend
```

### LLM Multi-Agent Mode

Для proposal-aligned runtime с `OpenAI` embeddings + generation, `Mistral` как generation fallback 1 и `Anthropic` как generation fallback 2, в `.env` используется следующий набор:

```bash
PFIA_ORCHESTRATOR_BACKEND=langgraph
PFIA_RETRIEVAL_BACKEND=chroma
PFIA_CHROMA_MODE=embedded
PFIA_PII_BACKEND=regex+spacy
PFIA_PII_SPACY_RU_MODEL=ru_core_news_sm
PFIA_PII_SPACY_EN_MODEL=en_core_web_sm
PFIA_SENTIMENT_BACKEND=vader
PFIA_EMBEDDING_BACKEND=openai
PFIA_EMBEDDING_PRIMARY_MODEL=text-embedding-3-small
PFIA_EMBEDDING_FALLBACK_MODEL=paraphrase-multilingual-mpnet-base-v2
PFIA_GENERATION_BACKEND=openai
OPENAI_API_KEY=<your_openai_key>
MISTRAL_API_KEY=<your_mistral_key>
ANTHROPIC_API_KEY=<your_anthropic_key>
PFIA_LLM_PRIMARY_MODEL=gpt-4o-mini
PFIA_LLM_FALLBACK_MODEL=mistral-small-latest
PFIA_LLM_SECOND_FALLBACK_MODEL=claude-3-5-haiku-latest
PFIA_LLM_MAX_TOOL_STEPS=4
```

Чтобы privacy path действительно использовал `spaCy`, а не только `regex`, локально устанавливаются модели:

```bash
make install-spacy-models
```

На Railway отдельная ручная установка не нужна: production Docker image по умолчанию ставит `en_core_web_sm` и `ru_core_news_sm` во время build.

Что меняется в этом режиме:

- clustering и Chroma indexing сначала пытаются использовать `text-embedding-3-small`;
- если embedding provider недоступен, runtime пытается перейти на local `sentence-transformers`, если он установлен в образе, а затем на deterministic projection fallback;
- privacy gate сначала использует regex, а при наличии локально установленных spaCy-моделей добавляет `ru_core_news_sm` / `en_core_web_sm` для masking person entities;
- sentiment scoring сначала пытается использовать `VADER`, а для `ru` и при отсутствии зависимости автоматически откатывается на lexical fallback;
- clustering engine прогоняет bounded HDBSCAN reflection loop: если silhouette ниже quality gate, он пробует до 3 профилей и сохраняет diagnostic trace в report/API;
- `PreprocessReviewAgent` делает second-pass review для borderline heuristic flags;
- `ClusterReviewAgent` делает LLM-guided merge/split review после детерминированной кластеризации;
- кластерные label/summary уточняются через `TaxonomyAgent`;
- anomaly explanations пишет `AnomalyExplainerAgent`;
- executive summary пишет `ExecutiveSummaryAgent`;
- Q&A выполняется через `QueryPlannerAgent` и `AnswerWriterAgent`, которые оркестрируют retrieval tools;
- при проблемах с OpenAI система сначала пытается перейти на `Mistral`, затем на `Anthropic`, и только потом откатывается на локальный deterministic fallback.

Признаки того, что используется внешний LLM path:

- в UI и `GET /api/sessions/{session_id}` появляется `runtime_metadata`;
- `runtime_profile` должен быть `llm-enhanced`;
- `orchestrator_backend_effective` должен быть `langgraph`;
- `pii_backend_effective` должен быть `regex` или `regex+spacy`;
- `sentiment_backend_effective` должен быть `lexical`, `vader`, `hybrid` или `mixed(...)`;
- `embedding_backend_effective` должен быть `openai`, `sentence-transformers`, `projection` или `mixed`;
- `generation_backend_effective` должен быть `openai`, `mistral`, `anthropic` или `mixed`;
- `retrieval_backend_effective` должен быть `chroma`;
- `chroma_mode_effective` должен быть `embedded` или `http`;
- `embedding_model_effective` должен быть `text-embedding-3-small`, `paraphrase-multilingual-mpnet-base-v2` или projection fallback model;
- `sentiment_model_effective` должен быть `vaderSentiment` или `n/a`;
- `trace_correlation_id` должен быть непустым;
- `trace_exporters_effective` должен показывать хотя бы `local-jsonl`;
- `trace_local_path` должен указывать на persisted JSONL trace file;
- `llm_call_count`, `embedding_call_count`, token totals и provider usage summary должны появляться в runtime metadata/report;
- в report `Run Diagnostics` должны появляться `Clustering backend`, `Selected clustering profile`, `Reflection attempts`;
- в `agent_usage` хотя бы часть batch-агентов должна иметь `used=true` и `mode=openai`, `mode=mistral` или `mode=anthropic`;
- в ответе `POST /api/sessions/{session_id}/chat` поле `degraded_mode` должно быть `false`.

Почему архитектура сделана именно так:

- deterministic core оставлен для parsing, privacy, dedupe, clustering, scoring, anomaly detection и state machine;
- LLM-слой используется там, где нужен semantic judgment, planning или human-readable interpretation;
- fallback обязателен, потому что PoC не должен зависеть от внешнего API как от единственной точки отказа.

Подробное обоснование вынесено в [docs/llm-runtime.md](docs/llm-runtime.md).

## Railway Deploy

Полный hosted deploy в Railway описан в [docs/deploy/railway.md](docs/deploy/railway.md).

Коротко, canonical профиль сейчас такой:

1. `api` service:
   - root directory: repository root;
   - config: [railway.json](railway.json);
   - volume mount: `/data`.
1. `frontend` service:
   - root directory: `frontend/`;
   - config: [frontend/railway.json](frontend/railway.json).
1. `chroma` service:
   - root directory: `chroma/`;
   - config: [chroma/railway.json](chroma/railway.json);
   - volume mount: `/data`.

Критичный инфраструктурный момент:

- production `api` image по умолчанию **не** ставит `sentence-transformers`, потому что это тащит `torch` и может раздувать image выше лимита Railway;
- поэтому в hosted standard profile embedding fallback идёт `OpenAI -> projection`, а не `OpenAI -> sentence-transformers -> projection`;
- при более высоких лимитах local embedding fallback может быть возвращён build arg'ом `PFIA_INSTALL_LOCAL_EMBEDDINGS=true`, но этот режим не является оптимальным дефолтом для Railway.

### Optional Tracing Sinks

PFIA always writes structured traces to local JSONL on the runtime volume.

Для optional external sinks можно задать:

```bash
LANGSMITH_TRACING=true
LANGSMITH_API_KEY=<your_langsmith_key>
LANGSMITH_PROJECT=pfia
LANGSMITH_ENDPOINT=https://api.smith.langchain.com
PFIA_OTEL_TRACING_ENABLED=true
OTEL_EXPORTER_OTLP_TRACES_ENDPOINT=https://your-otlp-endpoint/v1/traces
```

Поведение:

- local JSONL trace sink включён всегда;
- LangSmith и OTLP sinks включаются только если заданы env vars и доступны зависимости;
- observability sinks никогда не блокируют основной pipeline: при ошибке telemetry silently drops.

## Произвольный прогон

Проект не завязан на встроенный demo dataset. Через Next.js UI, fallback FastAPI UI и HTTP API можно загрузить произвольный CSV/JSON.

Минимальный входной контракт:

- обязательные поля:
  - `review_id`
  - `source`
  - `text`
  - `created_at`
- опциональные поля:
  - `rating`
  - `language`
  - `app_version`
- поддерживаемые `source`:
  - `app_store`
  - `google_play`
  - `zendesk`
  - `telegram`
  - `nps`
  - `email`
  - `web`

Пример произвольного CSV:

```csv
review_id,source,text,created_at,rating,language,app_version
demo_001,app_store,"Checkout crashes after I tap Pay",2026-04-12T09:00:00Z,1,en,5.5.1
demo_002,google_play,"Не приходит код входа уже 10 минут",2026-04-12T10:30:00Z,1,ru,5.5.1
demo_003,web,"The new onboarding flow feels much cleaner",2026-04-12T12:10:00Z,5,en,5.5.1
```

Пример произвольного JSON:

```json
[
  {
    "review_id": "demo_001",
    "source": "app_store",
    "text": "Checkout crashes after I tap Pay",
    "created_at": "2026-04-12T09:00:00Z",
    "rating": 1,
    "language": "en",
    "app_version": "5.5.1"
  }
]
```

Для воспроизводимого произвольного smoke-run есть параметризуемый скрипт:

```bash
python check.py --file path/to/reviews.csv --question "Which topic is spiking this week?"
```

Скрипт:

- загружает указанный файл;
- ждёт завершения batch-job;
- печатает `runtime_profile`, `orchestrator_backend_effective`, `generation_backend_effective`, `retrieval_backend_effective`, `input_filename`, `top_cluster_ids`;
- задаёт произвольный grounded question после завершения обработки.

При использовании другого файла, другого размера батча и другого вопроса меняются и ответы, и runtime metadata. Это базовая проверка того, что демо не hardcoded.

## Automated Acceptance Evals

Для proposal-aligned acceptance checks теперь есть отдельный runner:

```bash
make evals
```

или напрямую:

```bash
PFIA_DATA_DIR=data/runtime PYTHONPATH=src ./.venv/bin/python -m pfia.evals
```

Он автоматически проверяет:

- end-to-end batch completion;
- PII masking regression;
- retrieval на фиксированном наборе вопросов;
- trace/correlation metadata;
- recovery после simulated in-flight crash;
- наличие runtime metadata и runtime appendix в report.

## Демонстрационный сценарий

В репозитории уже лежит demo dataset:

- `data/demo/mobile_app_reviews.csv`

Характеристики demo dataset:

- `36` отзывов;
- `18` из `app_store`, `18` из `google_play`;
- период: с `2026-03-17` по `2026-04-12`;
- основные темы: payment flow crashes, login code delays, positive UX feedback.

Через UI можно:

1. Нажать `Run Demo Dataset`.
1. Дождаться завершения batch-job.
1. Посмотреть clusters, alerts и Markdown-отчёт.
1. Задать вопрос, например:
   - `What is the highest-priority issue and what evidence supports it?`
   - `Compare billing issues and login code delays.`
   - `Which topic is spiking this week?`

Ожидаемый demo outcome:

- top issue: `Payment flow crashes`
- явный spike по проблемам оплаты / crash
- Q&A отвечает grounded evidence с цитатами и cluster ids

## HTTP API

Основные endpoints:

- `POST /api/sessions/upload` - принять CSV/JSON и создать `session` + `job`
- `GET /api/sessions/{session_id}` - статус, clusters, alerts, report, event timeline
- `GET /api/sessions/{session_id}` также возвращает `runtime_metadata` с effective orchestrator / generation / retrieval backends и agent usage
- `GET /api/sessions/{session_id}/report` - report artifact
- `POST /api/sessions/{session_id}/chat` - grounded Q&A
- `GET /api/demo/sample-file` - скачать demo CSV
- `GET /health/live`
- `GET /health/ready`
- `GET /metrics`

## Что проверено в этом репозитории

Проверки, выполненные на текущем состоянии проекта:

- `pytest`: `38 passed`
- `npm --prefix frontend run build`
- `pfia.evals` acceptance harness
- локальный smoke batch-flow
- локальный smoke grounded Q&A
- `docker compose build`
- `docker compose up -d`
- e2e upload -> process -> chat через живой Docker API
- hosted Railway deployment:
  - `frontend` public URL отвечает;
  - `api` public URL отвечает;
  - `chroma` heartbeat отвечает;
  - `/health/live` возвращает `{"status":"ok"}`;
  - `/health/ready` возвращает `ready=true`, `worker.mode=embedded`, `storage.data_dir=/data/runtime`;
  - demo batch успешно доходит до `COMPLETED`;
  - `runtime_profile=llm-enhanced`;
  - `orchestrator_backend_effective=langgraph`;
  - `generation_backend_effective=openai`;
  - `embedding_backend_effective=openai`;
  - `retrieval_backend_effective=chroma`;
  - `chroma_mode_effective=http`;
  - hosted Q&A возвращает grounded answer по `payment_flow_crashes_1` без `degraded_mode`

## Качество и безопасность

В PoC уже заложены базовые guardrails:

- batch limit: до `2000` отзывов
- upload size limit: до `10 MB`
- queue depth limit
- PII masking до индексации и отчёта
- session-scoped retrieval
- persisted job/session state в SQLite
- recovery после рестарта worker через re-queue in-flight jobs
- metrics и stage events для диагностики

Тестами покрыто:

- полный batch-flow
- privacy masking
- recovery после рестарта worker
- grounded priority Q&A
- session runtime metadata
- external helper agents with provider fallback
- planner/writer Q&A path
- normalization structured writer output
- LLM preprocessing review
- LLM cluster review
- LLM anomaly explainer

## Подготовка к будущему деплою

Почва под деплой уже подготовлена:

- один и тот же slim Python image используется для `api` и `worker`
- все runtime-настройки вынесены в env vars
- state хранится в persistent volume
- есть health endpoints для orchestrator / platform probes
- есть `/metrics` для Prometheus-compatible scraping
- запуск не зависит от платных API-ключей
- есть service-specific Railway configs:
  - root [railway.json](railway.json) для `api`
  - [frontend/railway.json](frontend/railway.json) для `frontend`
  - [chroma/railway.json](chroma/railway.json) для `chroma`
- canonical hosted profile = `frontend + api + chroma`
- fallback single-service mode поддерживается, но не считается основным proposal-aligned deploy
- Railway volume path и `PORT` подхватываются автоматически

### Railway-first деплой

Для полного hosted deployment в текущем состоянии проекта базовой платформой считается `Railway`.

В репозитории уже лежит:

- отдельные Railway service configs для `api`, `frontend` и `chroma`;
- slim production `Dockerfile`, который по умолчанию не ставит `sentence-transformers`, чтобы не раздувать image выше лимитов Railway;
- отдельный [chroma/Dockerfile](chroma/Dockerfile) для standalone Chroma service;
- platform-aware runtime:
  - если есть `RAILWAY_VOLUME_MOUNT_PATH`, приложение автоматически переносит runtime state в volume;
  - если есть platform `PORT`, API слушает его без дополнительных флагов;
  - если есть Railway volume, автоматически включается embedded worker.

Для первого деплоя требуется:

1. Создать Railway project из этого репозитория.
1. Поднять отдельные services:
   - `api` из repo root
   - `frontend` из `frontend/`
   - `chroma` из `chroma/`
1. Подключить volume `/data` к `api`.
1. Подключить volume `/data` к `chroma`.
1. Задать private networking:
   - `frontend -> api`
   - `api -> chroma`
1. При необходимости включить OpenAI / Mistral / Anthropic runtime через Railway Variables.
1. Сгенерировать public domain для `frontend`.

Подробный runbook лежит в [docs/deploy/railway.md](docs/deploy/railway.md).

Для текущего live deployment user-facing URL такой:

- `frontend`: `https://frontend-production-c4b0.up.railway.app`

Технические smoke/health checks удобнее делать через:

- `api`: `https://api-production-242f.up.railway.app`

Технический heartbeat для Chroma:

- `chroma`: `https://chroma-production-4408.up.railway.app/api/v2/heartbeat`

## Документация

Проектные документы и дизайн-артефакты:

- [Product Proposal](docs/product-proposal.md)
- [System Design](docs/system-design.md)
- [Governance](docs/governance.md)
- [Module Specs](docs/specs/README.md)
- [Architecture Diagrams](docs/diagrams/README.md)
- [Railway Deploy Runbook](docs/deploy/railway.md)
- [Async Review Guide](docs/review-guide.md)
- [Testing Playbook](docs/testing-playbook.md)
- [LLM Runtime Strategy](docs/llm-runtime.md)

Основные документы для сдачи и деплоя: этот `README`, [docs/testing-playbook.md](docs/testing-playbook.md), [docs/llm-runtime.md](docs/llm-runtime.md) и фактический код в репозитории.
