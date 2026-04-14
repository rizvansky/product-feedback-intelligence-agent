# Railway Deploy Runbook

Этот документ фиксирует **канонический Railway deploy** для текущего состояния PFIA. Он рассчитан на полный hosted-профиль, наиболее близкий к текущим `.md`-документам и runtime-контрактам:

- `frontend` - отдельный `Next.js` service;
- `api` - FastAPI API + embedded worker;
- `chroma` - отдельный Chroma HTTP-service;
- persistent volumes для `api` и `chroma`.

В конце отдельно описан упрощённый fallback-профиль `api-only`.

## Current Verified Deployment

Публичные URL текущего проверенного hosted-профиля:

- `frontend`: `https://frontend-production-c4b0.up.railway.app`
- `api`: `https://api-production-242f.up.railway.app`
- `chroma` heartbeat: `https://chroma-production-4408.up.railway.app/api/v2/heartbeat`

В текущем production deployment:

- user-facing traffic идёт через `frontend`;
- smoke и health checks удобнее гонять через `api`;
- Chroma подключён в `api` как HTTP backend;
- текущий рабочий endpoint Chroma в runtime metadata выглядит как `https://chroma-production-4408.up.railway.app:443`.

## 1. Что именно деплоим

### 1.1 `api`

- Root directory: repository root
- Config file: [railway.json](../../railway.json)
- Dockerfile: [Dockerfile](../../Dockerfile)
- Role:
  - FastAPI API
  - embedded worker
  - SQLite state
  - reports / traces / sanitized artifacts
  - retrieval orchestration against external `chroma`

### 1.2 `frontend`

- Root directory: `frontend/`
- Config file: [frontend/railway.json](../../frontend/railway.json)
- Dockerfile: [frontend/Dockerfile](../../frontend/Dockerfile)
- Role:
  - public `Next.js` UI
  - same-origin browser surface
  - rewrite proxy `/pfia/* -> api`

### 1.3 `chroma`

- Root directory: `chroma/`
- Config file: [chroma/railway.json](../../chroma/railway.json)
- Dockerfile: [chroma/Dockerfile](../../chroma/Dockerfile)
- Role:
  - standalone Chroma server
  - persistent vector collections
  - HTTP endpoint for `api`

## 2. Railway Project Layout

В одном Railway project используются **3 services**:

1. `api`
1. `frontend`
1. `chroma`

И **2 volumes**:

1. volume для `api` -> mount path `/data`
1. volume для `chroma` -> mount path `/data`

`frontend` volume не нужен.

## 3. Service Setup

### 3.1 `api` service

`api` service собирается из repository root.

Ожидаемый config:

- root directory: `/`
- `railway.json` из repo root
- required mount path: `/data`

Что делает этот service:

- сам слушает HTTP;
- сам поднимает embedded worker;
- пишет runtime state в `/data/runtime`.

### 3.2 `frontend` service

`frontend` service собирается из поддиректории `frontend/`.

Ожидаемый config:

- root directory: `frontend`
- `frontend/railway.json`
- публичный домен должен быть выдан именно этому service

### 3.3 `chroma` service

`chroma` service собирается из поддиректории `chroma/`.

Ожидаемый config:

- root directory: `chroma`
- `chroma/railway.json`
- volume mount: `/data`

Этот service стартует через:

```bash
chroma run --host 0.0.0.0 --port ${PORT:-8000} --path /data/chroma
```

Healthcheck:

- `/api/v2/heartbeat`

## 4. Variables

### 4.1 `api` variables

Минимально рекомендуемый production набор:

```text
PFIA_ENV=prod
PFIA_LOG_LEVEL=INFO
PFIA_MAX_QUEUE_DEPTH=3
PFIA_MAX_BATCH_SIZE=2000

PFIA_ORCHESTRATOR_BACKEND=langgraph
PFIA_RETRIEVAL_BACKEND=chroma
PFIA_CHROMA_MODE=http
PFIA_CHROMA_HOST=<private-chroma-host>
PFIA_CHROMA_PORT=8000
PFIA_CHROMA_SSL=false

PFIA_PII_BACKEND=regex+spacy
PFIA_SENTIMENT_BACKEND=vader

PFIA_EMBEDDING_BACKEND=openai
PFIA_EMBEDDING_PRIMARY_MODEL=text-embedding-3-small
PFIA_EMBEDDING_FALLBACK_MODEL=paraphrase-multilingual-mpnet-base-v2

PFIA_GENERATION_BACKEND=openai
PFIA_LLM_PRIMARY_MODEL=gpt-4o-mini
PFIA_LLM_FALLBACK_MODEL=mistral-small-latest
PFIA_LLM_SECOND_FALLBACK_MODEL=claude-3-5-haiku-latest
PFIA_LLM_MAX_TOOL_STEPS=4

OPENAI_API_KEY=<your_openai_key>
MISTRAL_API_KEY=<your_mistral_key>
ANTHROPIC_API_KEY=<your_anthropic_key>
```

Для current verified deployment также используется рабочий набор Chroma-переменных через public TLS endpoint:

```text
PFIA_CHROMA_MODE=http
PFIA_CHROMA_HOST=chroma-production-4408.up.railway.app
PFIA_CHROMA_PORT=443
PFIA_CHROMA_SSL=true
```

При стабильном private networking вместо этого используется private hostname `chroma.railway.internal` и внутренний порт `8000`.

Опционально:

```text
PFIA_OPENAI_TIMEOUT_S=30
PFIA_OPENAI_MAX_RETRIES=2
LANGSMITH_TRACING=true
LANGSMITH_API_KEY=<your_langsmith_key>
LANGSMITH_PROJECT=pfia
LANGSMITH_ENDPOINT=https://api.smith.langchain.com
PFIA_OTEL_TRACING_ENABLED=true
OTEL_EXPORTER_OTLP_TRACES_ENDPOINT=<your_otlp_endpoint>
```

Что **не нужно** вручную задавать:

- `PFIA_PORT`
- `PORT`
- `PFIA_DATA_DIR`
- `PFIA_EMBEDDED_WORKER`

Они вычисляются платформой и кодом автоматически.

### 4.2 `frontend` variables

Нужны:

```text
NEXT_PUBLIC_PFIA_API_BASE_URL=/pfia
PFIA_INTERNAL_API_BASE_URL=http://<api-private-host>:8080
```

Смысл:

- browser остаётся на frontend domain;
- Next.js proxy переправляет `/pfia/*` на private backend endpoint;
- CORS не нужен.

### 4.3 `chroma` variables

Минимально достаточно:

```text
IS_PERSISTENT=TRUE
```

Если service использует volume `/data`, этого достаточно.

## 5. Private Networking

Для корректной связи сервисов нужны два private адреса:

1. `frontend -> api`
1. `api -> chroma`

Публичные домены не используются там, где должен идти internal traffic.

Для internal traffic используются private hostnames / service-internal addresses Railway.

Рабочая схема адресации:

- в `frontend`:
  - `PFIA_INTERNAL_API_BASE_URL=http://<api-private-host>:8080`
- в `api`:
  - `PFIA_CHROMA_HOST=<chroma-private-host>`
  - `PFIA_CHROMA_PORT=8000`

Для текущего live deployment user-facing/public path используется так:

- `frontend` browser traffic: public `frontend` domain;
- `frontend -> api`: internal rewrite target;
- `api -> chroma`: public TLS Chroma endpoint.

## 6. Public Networking

Публичный домен нужен только для `frontend`.

Дополнительно публичный домен для `api` можно выдать, если требуется отдельно проверять:

- `/health/live`
- `/health/ready`
- `/metrics`
- `POST /api/sessions/upload`

Но основной user-facing URL должен быть у `frontend`.

## 7. Deployment Order

Правильный порядок:

1. Создать `chroma` service и прикрепить volume `/data`.
1. Задеплоить `chroma`.
1. Проверить `chroma` healthcheck.
1. Создать `api` service и прикрепить volume `/data`.
1. Задать `api` variables, включая `PFIA_CHROMA_HOST` и `PFIA_CHROMA_PORT`.
1. Задеплоить `api`.
1. Проверить `/health/live` и `/health/ready`.
1. Создать `frontend` service.
1. Задать `NEXT_PUBLIC_PFIA_API_BASE_URL=/pfia`.
1. Задать `PFIA_INTERNAL_API_BASE_URL` на private `api` host.
1. Задеплоить `frontend`.
1. Сгенерировать public domain для `frontend`.

## 8. Build Strategy Notes

### 8.1 Build profile `api`

В [Dockerfile](../../Dockerfile) используется:

- `PFIA_INSTALL_LOCAL_EMBEDDINGS=false` по умолчанию
- conditional install:
  - `pip install .`
  - или `pip install ".[local-embeddings]"`

Это означает:

- Railway `api` build не тянет `torch` и CUDA-пакеты без явного запроса;
- production image остаётся slim;
- hosted default profile для embeddings: `OpenAI -> projection fallback`.

### 8.2 Когда включать `PFIA_INSTALL_LOCAL_EMBEDDINGS=true`

Только если:

- действительно требуется `sentence-transformers` fallback inside hosted image;
- Railway plan выдерживает более тяжёлый image;
- допустим более медленный build.

Для стандартного Railway deploy это не требуется.

## 9. Verification Checklist

### 9.1 `chroma`

Проверить:

- service healthy
- volume mounted at `/data`
- heartbeat отвечает:
  - `/api/v2/heartbeat`

### 9.2 `api`

Проверить:

```text
/health/live
/health/ready
/metrics
```

Ожидаемо:

- `/health/live` -> `200`
- `/health/ready` -> `200`
- `worker.mode=embedded`
- `storage.data_dir=/data/runtime`
- текущий smoke-run через `check.py` должен показывать:
  - `orchestrator_backend_effective=langgraph`
  - `generation_backend_effective=openai`
  - `embedding_backend_effective=openai`
  - `retrieval_backend_effective=chroma`
  - `chroma_mode_effective=http`

### 9.3 `frontend`

Проверка `frontend` service через public domain:

1. `Run Demo Dataset`
1. статус `COMPLETED`
1. clusters / report / runtime metadata видны
1. Q&A отвечает grounded evidence

## 10. Runtime Metadata Expectations

После успешного hosted run в `GET /api/sessions/{session_id}` ожидаемо:

- `runtime_profile=llm-enhanced` при включённых provider keys
- `orchestrator_backend_effective=langgraph`
- `retrieval_backend_effective=chroma`
- `chroma_mode_effective=http`
- `chroma_endpoint_effective` указывает на private `chroma` endpoint
- `pii_backend_effective=regex+spacy`
- `generation_backend_effective=openai`, `mistral`, `anthropic` или `mixed`
- `embedding_backend_effective=openai`, `projection` или `mixed`

Важно:

- если local `sentence-transformers` не установлен в hosted image, fallback логично уйдёт не в `sentence-transformers`, а в `projection`;
- это ожидаемое поведение для slim Railway build.

## 11. End-to-End Smoke

После deploy:

```bash
python check.py --base-url https://api-production-242f.up.railway.app
```

Если публичного URL у `api` нет, проверяй через `frontend` UI и сравни:

- session status
- runtime metadata
- Q&A answer

Дополнительно загрузить произвольный CSV/JSON и убедиться, что:

- `runtime_metadata.input_filename` меняется;
- `top_cluster_ids` меняются;
- report/Q&A зависят от реально загруженного файла.

## 12. Common Failure Modes

### `api` build тянет гигантский image

Причина:

- в build включён local embeddings stack.

Что делать:

- не включать `PFIA_INSTALL_LOCAL_EMBEDDINGS=true` в Railway `api` service.

### `frontend` открывается, но API requests падают

Причина:

- неверный `PFIA_INTERNAL_API_BASE_URL`
- frontend смотрит не в private `api`

Что делать:

- проверить `frontend` variables
- проверить, что proxy target указывает на Railway internal hostname `api`

### `api` ready, но retrieval не работает

Причина:

- `PFIA_CHROMA_MODE=http` не задан
- неверный `PFIA_CHROMA_HOST`
- `chroma` service unhealthy

Что делать:

- проверить `api` variables
- проверить `chroma` health
- проверить `runtime_metadata.chroma_mode_effective` и `chroma_endpoint_effective`

### `chroma` service собран как PFIA API

Причина:

- для `chroma` выбран не тот root directory / config

Что делать:

- root directory должен быть `chroma/`
- config должен быть `chroma/railway.json`

## 13. Optional Fallback Profile

Для быстрого и упрощённого развертывания остаётся доступен следующий профиль:

- только `api` service
- встроенный FastAPI UI
- embedded worker
- embedded Chroma/persisted index

Этот профиль не считается основным proposal-aligned deploy. Полный hosted профиль задаётся схемой `frontend + api + chroma`.
