# Product Feedback Intelligence Agent (PFIA)

PFIA - это PoC-система для пакетного анализа пользовательского фидбека: загрузка CSV/JSON, анонимизация PII, тематическая кластеризация, приоритизация проблем, anomaly detection, Markdown-отчёт и grounded Q&A по уже обработанной сессии.

Текущий репозиторий уже содержит рабочий PoC, который можно запустить локально без внешних API-ключей. Для локального demo по умолчанию используется offline-профиль: локальная аналитика, SQLite и persistent Chroma-backed retrieval с projection fallback. При наличии `OPENAI_API_KEY` можно включить proposal-grade embedding path (`text-embedding-3-small`) и LLM-backed multi-agent runtime, а при сбое внешних provider'ов сервис автоматически возвращается к local sentence-transformers или deterministic fallback path.

## Live Demo

Текущий публичный деплой, проверенный 13 апреля 2026 года:

- Demo URL: `https://product-feedback-intelligence-agent-production.up.railway.app`
- GitHub repository: `https://github.com/rizvansky/product-feedback-intelligence-agent`

Быстрая проверка hosted PoC занимает 1-2 минуты:

1. Открыть demo URL.
1. Нажать `Run Demo Dataset`.
1. Дождаться статуса `COMPLETED`.
1. Убедиться, что в UI появились clusters, Markdown report и timeline events.
1. В Q&A задать: `What is the highest-priority issue and what evidence supports it?`

Ожидаемый результат:

- top issue: `Payment flow crashes`
- количество кластеров на demo batch: `8`
- health endpoints доступны:
  - `/health/live`
  - `/health/ready`
  - `/metrics`

## Что Реализовано

- Upload отзывов в формате CSV/JSON через web UI и HTTP API.
- Асинхронный batch-flow через `job` + отдельный worker process.
- Privacy gate:
  - masking email / phone / device-like identifiers;
  - quarantine при residual PII;
  - raw PII не сохраняется в sanitized artifacts, отчётах и retrieval.
- Локальный analysis pipeline:
  - multilingual preprocessing;
  - concept-enhanced clustering;
  - priority scoring;
  - anomaly detection по weekly baseline;
  - Markdown report + executive summary.
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
  - trace correlation id;
  - effective backend;
  - effective PII backend;
  - effective sentiment backend;
  - effective retrieval backend;
  - provider call counts and token totals;
  - input filename;
  - records kept;
  - top cluster ids;
  - per-agent usage snapshot.
- Health endpoints, Prometheus-compatible `/metrics`, Docker Compose, demo dataset и тесты.
- Hosted deploy profile под Railway: single-service режим с embedded worker и persistent volume.

## Реальный Stack В Репозитории

Текущая имплементация намеренно проще изначального target design:

- `api`: FastAPI-приложение, которое одновременно отдаёт HTTP API и статический frontend.
- `worker`: отдельный Python worker для очереди jobs.
- `storage`: SQLite + локальные файлы артефактов + persistent Chroma collections + session pickle fallback для retrieval.
- `frontend`: статический UI, встроенный в web service.

Это сделано сознательно ради PoC и будущего деплоя с минимальным operational overhead. Логические границы модулей из проектной документации сохранены, но контейнерная топология упрощена.

Для hosted deployment в репозитории также подготовлен single-service профиль:

- `api + embedded worker`: один web-service process с фоновым worker thread.
- `storage`: Railway volume, автоматически подхватываемый через `RAILWAY_VOLUME_MOUNT_PATH`.
- `port binding`: приложение понимает как `PFIA_PORT`, так и platform-provided `PORT`.

Такой профиль удобнее для SQLite/file-based PoC, чем разнос `api` и `worker` по отдельным hosted services без общего локального диска.

## Быстрый Старт

### Вариант 1. Docker Compose

Требования:

- Docker
- Docker Compose

Запуск:

```bash
docker compose up --build
```

После старта открой:

- UI: `http://localhost:8000`
- live health: `http://localhost:8000/health/live`
- ready health: `http://localhost:8000/health/ready`
- metrics: `http://localhost:8000/metrics`

`docker-compose.yml` использует `.env.example` с безопасными offline-дефолтами, так что для demo дополнительная настройка не нужна.

Примечание: внутри Docker data dir принудительно переопределяется в `/app/data/runtime`, поэтому локальный host-path и контейнерный path не конфликтуют.
Примечание: local `docker compose` по умолчанию собирает image без `spaCy`-моделей, чтобы сборка оставалась легче и быстрее.

Если нужен proposal-grade privacy path в Docker локально:

```bash
PFIA_INSTALL_SPACY_MODELS=true docker compose build
docker compose up -d
```

Остановка:

```bash
docker compose down -v
```

### Вариант 2. Локальный Запуск Без Docker

Требования:

- Python 3.10+
- виртуальное окружение

Установка зависимостей:

```bash
./.venv/bin/python -m pip install -e ".[dev]"
```

Запуск API:

```bash
PYTHONPATH=src ./.venv/bin/python -m pfia.api
```

Запуск worker в отдельном терминале:

```bash
PYTHONPATH=src ./.venv/bin/python -m pfia.worker
```

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
make demo
```

### LLM Multi-Agent Mode

Чтобы включить proposal-aligned runtime с `OpenAI` embeddings + generation, `Mistral` как generation fallback 1 и `Anthropic` как generation fallback 2, задай в `.env`:

```bash
PFIA_ORCHESTRATOR_BACKEND=langgraph
PFIA_RETRIEVAL_BACKEND=chroma
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

Чтобы privacy path действительно использовал `spaCy`, а не только `regex`, локально установи модели:

```bash
make install-spacy-models
```

На Railway отдельная ручная установка не нужна: production Docker image по умолчанию ставит `en_core_web_sm` и `ru_core_news_sm` во время build.

Что меняется в этом режиме:

- clustering и Chroma indexing сначала пытаются использовать `text-embedding-3-small`;
- если embedding provider недоступен, runtime пытается перейти на local `sentence-transformers`, а затем на deterministic projection fallback;
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

Как убедиться, что сработал именно внешний LLM path:

- в UI и `GET /api/sessions/{session_id}` появляется `runtime_metadata`;
- `runtime_profile` должен быть `llm-enhanced`;
- `orchestrator_backend_effective` должен быть `langgraph`;
- `pii_backend_effective` должен быть `regex` или `regex+spacy`;
- `sentiment_backend_effective` должен быть `lexical`, `vader`, `hybrid` или `mixed(...)`;
- `embedding_backend_effective` должен быть `openai`, `sentence-transformers`, `projection` или `mixed`;
- `generation_backend_effective` должен быть `openai`, `mistral`, `anthropic` или `mixed`;
- `retrieval_backend_effective` должен быть `chroma`;
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

## Произвольный Прогон

Проект не завязан на встроенный demo dataset. Через UI и API можно загрузить произвольный CSV/JSON.

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

Если взять другой файл, другие размеры батча и другой вопрос, ответы и runtime metadata будут другими. Это самый простой способ показать, что демо не hardcoded.

## Демонстрационный Сценарий

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

## Что Проверено В Этом Репозитории

Проверки, выполненные на текущем состоянии проекта:

- `pytest`: `12 passed`
- локальный smoke batch-flow
- локальный smoke grounded Q&A
- `docker compose build`
- `docker compose up -d`
- e2e upload -> process -> chat через живой Docker API
- hosted Railway deployment:
  - public URL отвечает;
  - `/health/live` возвращает `{"status":"ok"}`;
  - `/health/ready` возвращает `ready=true`, `worker.mode=embedded`, `storage.data_dir=/data/runtime`;
  - demo batch успешно доходит до `COMPLETED`;
  - hosted Q&A возвращает grounded answer по `payment_flow_crashes_1`

## Качество И Безопасность

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

## Подготовка К Будущему Деплою

Почва под деплой уже подготовлена:

- один и тот же image используется и для `api`, и для `worker`
- все runtime-настройки вынесены в env vars
- state хранится в persistent volume
- есть health endpoints для orchestrator / platform probes
- есть `/metrics` для Prometheus-compatible scraping
- запуск не зависит от платных API-ключей
- есть `railway.json` для config-as-code
- single-service hosted mode не требует отдельного worker service
- Railway volume path и `PORT` подхватываются автоматически

### Railway-First Деплой

Для самого простого hosted deployment в текущем состоянии проекта рекомендован `Railway`.

В репозитории уже лежит:

- `railway.json`:
  - `DOCKERFILE` build;
  - `numReplicas=1` из-за SQLite + file artifacts;
  - healthcheck на `/health/ready`;
  - обязательный mount path `/data`.
- platform-aware runtime:
  - если есть `RAILWAY_VOLUME_MOUNT_PATH`, приложение автоматически переносит runtime state в volume;
  - если есть platform `PORT`, API слушает его без дополнительных флагов;
  - если есть Railway volume, автоматически включается embedded worker.

Практически это означает, что для первого деплоя позже понадобится:

1. Создать Railway project из этого репозитория.
1. Подключить один volume и смонтировать его в `/data`.
1. При необходимости включить OpenAI runtime через Railway Variables.
1. Сгенерировать public domain.

Подробный runbook лежит в [docs/deploy/railway.md](docs/deploy/railway.md).

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

Важно: часть документов описывает более широкий target design, чем текущая PoC-реализация. Для сдачи и деплоя ориентироваться стоит прежде всего на этот `README`, [docs/testing-playbook.md](docs/testing-playbook.md), [docs/llm-runtime.md](docs/llm-runtime.md) и фактический код в репозитории.
