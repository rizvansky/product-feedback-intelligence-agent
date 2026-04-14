# Testing Playbook

Этот playbook нужен для двух задач:

- быстро проверить, что PFIA действительно работает end-to-end;
- показать, что система не зашита под один встроенный demo dataset.

## 1. Базовый regression set

Перед пушем ожидаемый минимум:

```bash
./.venv/bin/python -m pytest -q
```

Сейчас test suite покрывает:

- полный deterministic batch-flow;
- privacy masking в sanitized artifacts и report;
- recovery после рестарта worker;
- grounded priority Q&A;
- Railway hosting defaults;
- embedded worker readiness;
- external taxonomy/report agents with provider fallback;
- OpenAI planner/writer Q&A;
- normalization structured writer output;
- LLM preprocessing review;
- LLM cluster review;
- LLM anomaly explainer;
- session runtime metadata.

## 2. Локальный deterministic smoke

Запуск сервиса:

```bash
make run-embedded
```

Проверка:

```bash
python check.py
```

Что ожидается:

- batch доходит до `COMPLETED` или `DEGRADED_COMPLETED`;
- `runtime_profile=deterministic`;
- `orchestrator_backend_effective=langgraph`;
- `generation_backend_effective=local`;
- `retrieval_backend_effective=chroma`;
- `pii_backend_effective=regex`, если `spaCy`-модели отдельно не устанавливались;
- `trace_correlation_id` непустой;
- `trace_exporters_effective` содержит `local-jsonl`;
- chat отвечает без ошибок.

## 3. Локальный LLM smoke

В `.env`:

```bash
PFIA_GENERATION_BACKEND=openai
PFIA_EMBEDDING_BACKEND=openai
OPENAI_API_KEY=<your_key>
MISTRAL_API_KEY=<your_key_optional_fallback>
ANTHROPIC_API_KEY=<your_key_optional_second_fallback>
PFIA_EMBEDDING_PRIMARY_MODEL=text-embedding-3-small
PFIA_EMBEDDING_FALLBACK_MODEL=paraphrase-multilingual-mpnet-base-v2
PFIA_LLM_PRIMARY_MODEL=gpt-4o-mini
PFIA_LLM_FALLBACK_MODEL=mistral-small-latest
PFIA_LLM_SECOND_FALLBACK_MODEL=claude-3-5-haiku-latest
PFIA_LLM_MAX_TOOL_STEPS=4
```

Запуск:

```bash
make run-embedded
python check.py
```

Что ожидается:

- batch доходит до `COMPLETED` или `DEGRADED_COMPLETED`;
- `runtime_profile=llm-enhanced`;
- `orchestrator_backend_effective=langgraph`;
- `pii_backend_effective=regex+spacy`, если image собран с `PFIA_INSTALL_SPACY_MODELS=true` или модели поставлены локально через `make install-spacy-models`;
- `embedding_backend_effective=openai`, `sentence-transformers`, `projection` или `mixed`;
- `generation_backend_effective=openai`, `mistral`, `anthropic` или `mixed`;
- `trace_exporters_effective` содержит `local-jsonl`, а при внешних sinks может также содержать `langsmith` и/или `otlp`;
- `retrieval_backend_effective=chroma`;
- в runtime metadata появляются `trace_correlation_id`, `llm_call_count`, `embedding_call_count` и token totals;
- в `runtime_metadata.agent_usage` видны `mode=openai`, `mode=mistral` или `mode=anthropic` хотя бы у части batch-агентов;
- chat возвращает `degraded_mode=false`.

## 4. Fallback smoke

Чтобы проверить graceful fallback:

1. оставить `PFIA_GENERATION_BACKEND=openai`;
1. подставить валидные `MISTRAL_API_KEY` и `ANTHROPIC_API_KEY`;
1. очистить `OPENAI_API_KEY` или подставить заведомо битый ключ;
1. перезапустить сервис;
1. снова выполнить `python check.py`.

Что ожидается:

- сервис не падает;
- batch всё ещё завершается;
- `runtime_profile=llm-enhanced`;
- `generation_backend_effective=mistral` или `anthropic`;
- `orchestrator_backend_effective` остаётся `langgraph` или уходит в `linear`, если ты специально тестируешь fallback orchestration;
- chat отвечает без ухода в local fallback.

Если очистить `OPENAI_API_KEY`, `MISTRAL_API_KEY` и `ANTHROPIC_API_KEY`, тогда ожидаемо:

- `runtime_profile=deterministic`;
- `generation_backend_effective=local`;
- chat отвечает через local fallback path.

## 5. Arbitrary run

Самый важный сценарий для демонстрации того, что проект не hardcoded:

```bash
python check.py \
  --file path/to/your_reviews.csv \
  --question "Which topic is spiking this week?"
```

Требования к входному файлу:

- форматы: `CSV` или `JSON`;
- обязательные поля: `review_id`, `source`, `text`, `created_at`;
- опциональные поля: `rating`, `language`, `app_version`.

Что смотреть после такого прогона:

- `INPUT_FILENAME` должен совпадать с твоим файлом;
- `TOP_CLUSTER_IDS` должны отличаться, если меняется сам датасет;
- `records_total` и `records_kept` меняются вместе с файлом;
- содержимое report и Q&A должно зависеть от реально загруженного текста.

## 6. UI / API proof points

Для session detail:

```bash
curl -s http://127.0.0.1:8000/api/sessions/<session_id>
```

Важно проверить:

- `session.status`
- `job.stage`
- `runtime_metadata.runtime_profile`
- `runtime_metadata.orchestrator_backend_effective`
- `runtime_metadata.generation_backend_effective`
- `runtime_metadata.retrieval_backend_effective`
- `runtime_metadata.input_filename`
- `runtime_metadata.agent_usage`

Для chat:

```bash
curl -s http://127.0.0.1:8000/api/sessions/<session_id>/chat \
  -H 'Content-Type: application/json' \
  -d '{"question":"What is the highest-priority issue and what evidence supports it?"}'
```

Важно проверить:

- `answer`
- `tool_trace`
- `degraded_mode`

## 7. Hosted Railway smoke

После деплоя:

- открыть `/health/live`;
- открыть `/health/ready`;
- открыть UI;
- выполнить `Run Demo Dataset`;
- выполнить хотя бы один произвольный upload своего CSV/JSON;
- сверить `runtime_metadata` и содержимое отчёта.

Если на Railway включены `OpenAI`, `Mistral` и/или `Anthropic`, для demo dataset и произвольного файла `runtime_profile` должен становиться `llm-enhanced`, а не оставаться deterministic-only.
