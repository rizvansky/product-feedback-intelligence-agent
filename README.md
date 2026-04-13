# Product Feedback Intelligence Agent (PFIA)

PFIA - это PoC-система для пакетного анализа пользовательского фидбека: загрузка CSV/JSON, анонимизация PII, тематическая кластеризация, приоритизация проблем, anomaly detection, Markdown-отчёт и grounded Q&A по уже обработанной сессии.

Текущий репозиторий уже содержит рабочий PoC, который можно запустить локально без внешних API-ключей. Для локального demo по умолчанию используется offline-профиль: локальная аналитика, SQLite и persisted retrieval index на диске. При наличии `OPENAI_API_KEY` можно включить OpenAI-backed multi-agent runtime, а при сбое или отсутствии ключа сервис автоматически возвращается к deterministic fallback path.

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
- Health endpoints, Prometheus-compatible `/metrics`, Docker Compose, demo dataset и тесты.
- Hosted deploy profile под Railway: single-service режим с embedded worker и persistent volume.

## Реальный Stack В Репозитории

Текущая имплементация намеренно проще изначального target design:

- `api`: FastAPI-приложение, которое одновременно отдаёт HTTP API и статический frontend.
- `worker`: отдельный Python worker для очереди jobs.
- `storage`: SQLite + локальные файлы артефактов + persisted retrieval index на диске.
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

### OpenAI Multi-Agent Mode

Чтобы включить настоящий OpenAI-backed multi-agent runtime, задай в `.env`:

```bash
PFIA_GENERATION_BACKEND=openai
OPENAI_API_KEY=<your_openai_key>
PFIA_LLM_PRIMARY_MODEL=gpt-4o-mini
PFIA_LLM_MAX_TOOL_STEPS=4
```

Что меняется в этом режиме:

- `PreprocessReviewAgent` делает second-pass review для borderline heuristic flags;
- `ClusterReviewAgent` делает LLM-guided merge/split review после детерминированной кластеризации;
- кластерные label/summary уточняются через `TaxonomyAgent`;
- anomaly explanations пишет `AnomalyExplainerAgent`;
- executive summary пишет `ExecutiveSummaryAgent`;
- Q&A выполняется через `QueryPlannerAgent` и `AnswerWriterAgent`, которые оркестрируют retrieval tools;
- при проблемах с OpenAI система откатывается на локальный deterministic fallback вместо hard failure.

Почему архитектура сделана именно так:

- deterministic core оставлен для parsing, privacy, dedupe, clustering, scoring, anomaly detection и state machine;
- LLM-слой используется там, где нужен semantic judgment, planning или human-readable interpretation;
- fallback обязателен, потому что PoC не должен зависеть от внешнего API как от единственной точки отказа.

Подробное обоснование вынесено в [docs/llm-runtime.md](docs/llm-runtime.md).

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
1. Опционально добавить `OPENAI_API_KEY`, если захочется расширить PoC внешней генерацией.
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

Важно: часть документов описывает более широкий target design, чем текущая PoC-реализация. Для сдачи ориентироваться стоит прежде всего на этот `README` и фактический код в репозитории.
