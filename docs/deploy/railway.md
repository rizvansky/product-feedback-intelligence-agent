# Railway Deploy Runbook

Этот runbook описывает рекомендуемый hosted deploy для текущего PoC: один Railway web service + один persistent volume. Для этого проекта это лучший operational tradeoff, потому что runtime state хранится в SQLite, report artifacts и persistent Chroma storage на локальном диске.

## Целевой Профиль

- один сервис `pfia-web`
- один Railway volume, смонтированный в `/data`
- одна реплика
- встроенный background worker внутри web process
- platform domain Railway на первом этапе

Почему именно так:

- `SQLite + local artifacts` плохо сочетаются с разнесением `api` и `worker` по разным hosted services без общего диска
- один сервис даёт самый быстрый путь к публичному URL
- `railway.json` уже зафиксировал безопасные deploy defaults

## Что Уже Подготовлено В Репозитории

- `Dockerfile` для production-style container build
- `railway.json` с:
  - `DOCKERFILE` builder
  - `startCommand=pfia-api --host 0.0.0.0`
  - `healthcheckPath=/health/ready`
  - `numReplicas=1`
  - `requiredMountPath=/data`
- platform-aware config:
  - `PORT` подхватывается автоматически
  - `RAILWAY_VOLUME_MOUNT_PATH` автоматически переводит runtime state в persistent volume
  - наличие Railway volume автоматически включает embedded worker mode

## Порядок Деплоя

1. Запушить актуальную ветку в GitHub.
1. В Railway создать новый проект из этого репозитория.
1. Убедиться, что сервис собрался из `Dockerfile`.
1. Добавить volume к этому сервису и смонтировать его в `/data`.
1. Запустить деплой.
1. После успешного старта сгенерировать public domain.

На минимальном PoC-профиле дополнительные env vars не обязательны.

## Рекомендуемые Переменные

Базовый deploy может работать и без них, но полезно явно зафиксировать:

- `PFIA_ENV=prod`
- `PFIA_LOG_LEVEL=INFO`
- `PFIA_MAX_QUEUE_DEPTH=3`
- `PFIA_MAX_BATCH_SIZE=2000`
- `PFIA_ORCHESTRATOR_BACKEND=langgraph`
- `PFIA_RETRIEVAL_BACKEND=chroma`
- `PFIA_PII_BACKEND=regex+spacy`
- `PFIA_SENTIMENT_BACKEND=vader`
- `PFIA_EMBEDDING_BACKEND=openai`

Опционально:

- `PFIA_GENERATION_BACKEND=openai`, если нужен LLM-backed runtime
- `OPENAI_API_KEY`, если нужен primary provider OpenAI
- `MISTRAL_API_KEY`, если нужен fallback provider Mistral
- `ANTHROPIC_API_KEY`, если нужен tertiary fallback provider Anthropic
- `PFIA_LLM_PRIMARY_MODEL=gpt-4o-mini`
- `PFIA_LLM_FALLBACK_MODEL=mistral-small-latest`
- `PFIA_LLM_SECOND_FALLBACK_MODEL=claude-3-5-haiku-latest`
- `PFIA_EMBEDDING_PRIMARY_MODEL=text-embedding-3-small`
- `PFIA_EMBEDDING_FALLBACK_MODEL=paraphrase-multilingual-mpnet-base-v2`
- `PFIA_LLM_MAX_TOOL_STEPS=4`
- `OPENAI_BASE_URL`, если будет прокси или совместимый endpoint
- `MISTRAL_BASE_URL`, если нужен совместимый endpoint
- `ANTHROPIC_BASE_URL`, если нужен совместимый endpoint

Не нужно вручную задавать:

- `PFIA_PORT`: Railway сам передаст `PORT`
- `PFIA_DATA_DIR`: при наличии volume путь будет вычислен автоматически
- `PFIA_EMBEDDED_WORKER`: при наличии Railway volume режим включится автоматически

## Как Включить LLM Providers На Railway

Добавить в Railway service variables:

- `PFIA_GENERATION_BACKEND=openai`
- `PFIA_ORCHESTRATOR_BACKEND=langgraph`
- `PFIA_RETRIEVAL_BACKEND=chroma`
- `PFIA_EMBEDDING_BACKEND=openai`
- `OPENAI_API_KEY=<your_openai_key>`
- `MISTRAL_API_KEY=<your_mistral_key>`
- `ANTHROPIC_API_KEY=<your_anthropic_key>`
- `PFIA_EMBEDDING_PRIMARY_MODEL=text-embedding-3-small`
- `PFIA_EMBEDDING_FALLBACK_MODEL=paraphrase-multilingual-mpnet-base-v2`
- `PFIA_LLM_PRIMARY_MODEL=gpt-4o-mini`
- `PFIA_LLM_FALLBACK_MODEL=mistral-small-latest`
- `PFIA_LLM_SECOND_FALLBACK_MODEL=claude-3-5-haiku-latest`
- `PFIA_LLM_MAX_TOOL_STEPS=4`
- опционально `PFIA_OPENAI_TIMEOUT_S=30`
- опционально `PFIA_OPENAI_MAX_RETRIES=2`

После этого сделать `Redeploy`.

Что изменится:

- batch-стадии смогут использовать `PreprocessReviewAgent`, `ClusterReviewAgent`, `TaxonomyAgent`, `AnomalyExplainerAgent`, `ExecutiveSummaryAgent`;
- privacy stage сможет реально использовать `regex+spacy`, потому что Railway image по умолчанию устанавливает `en_core_web_sm` и `ru_core_news_sm`;
- embeddings и Chroma indexing сначала пойдут через `text-embedding-3-small`, потом через local `sentence-transformers`, затем через deterministic projection fallback;
- Q&A пойдёт через `QueryPlannerAgent` и `AnswerWriterAgent`;
- при проблемах с `OpenAI` runtime сначала попытается перейти на `Mistral`, затем на `Anthropic`, и только потом вернётся к deterministic fallback path.

Как проверить, что на Railway реально работает внешний LLM path:

- выполнить batch-run;
- открыть `GET /api/sessions/{session_id}`;
- проверить, что `runtime_metadata.runtime_profile=llm-enhanced`;
- проверить, что `runtime_metadata.orchestrator_backend_effective=langgraph`;
- проверить, что `runtime_metadata.pii_backend_effective=regex+spacy`;
- проверить, что `runtime_metadata.embedding_backend_effective` равно `openai`, `sentence-transformers`, `projection` или `mixed`;
- проверить, что `runtime_metadata.generation_backend_effective` равно `openai`, `mistral`, `anthropic` или `mixed`;
- проверить, что `runtime_metadata.retrieval_backend_effective=chroma`;
- проверить, что в `runtime_metadata.agent_usage` есть `used=true` и `mode=openai`, `mode=mistral` или `mode=anthropic`;
- проверить, что `POST /api/sessions/{session_id}/chat` возвращает `degraded_mode=false`.

## Что Проверить После Деплоя

Открыть:

- `/health/live`
- `/health/ready`
- `/metrics`
- `/`

Ожидаемое состояние:

- `/health/live` возвращает `200`
- `/health/ready` возвращает `200`
- в `/health/ready` видно `worker.mode=embedded`
- `storage.data_dir` указывает на `/data/runtime`
- после batch-run `PII_BACKEND` в `check.py --base-url <url>` должен быть `regex+spacy`

Затем проверить пользовательский flow:

1. Открыть UI.
1. Нажать `Run Demo Dataset`.
1. Дождаться статуса `COMPLETED`.
1. Проверить, что report, clusters и runtime metadata отрисовались.
1. В Q&A спросить: `What is the highest-priority issue and what evidence supports it?`

## Ограничения Текущего Hosted Профиля

- не масштабировать выше `1` реплики
- не выносить API и worker в отдельные сервисы, пока state живёт в SQLite и локальных файлах
- не рассчитывать на zero-downtime redeploy при attached volume

## Следующий Шаг После Первого Деплоя

Когда этот профиль будет стабильно работать, можно переходить к следующему уровню:

- custom domain
- managed Postgres вместо SQLite
- object storage вместо локальных артефактов
- разнос `api` и `worker` на отдельные hosted services
- внешний LLM backend с секретами Railway
