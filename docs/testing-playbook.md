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
- OpenAI-backed taxonomy/report agents;
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
- `generation_backend_effective=local`;
- chat отвечает без ошибок.

## 3. Локальный OpenAI smoke

В `.env`:

```bash
PFIA_GENERATION_BACKEND=openai
OPENAI_API_KEY=<your_key>
PFIA_LLM_PRIMARY_MODEL=gpt-4o-mini
PFIA_LLM_MAX_TOOL_STEPS=4
```

Запуск:

```bash
make run-embedded
python check.py
```

Что ожидается:

- batch доходит до `COMPLETED` или `DEGRADED_COMPLETED`;
- `runtime_profile=openai-enhanced`;
- `generation_backend_effective=openai`;
- в `runtime_metadata.agent_usage` видны `mode=openai` хотя бы у части batch-агентов;
- chat возвращает `degraded_mode=false`.

## 4. Fallback smoke

Чтобы проверить graceful fallback:

1. оставить `PFIA_GENERATION_BACKEND=openai`;
1. очистить `OPENAI_API_KEY` или подставить заведомо битый ключ;
1. перезапустить сервис;
1. снова выполнить `python check.py`.

Что ожидается:

- сервис не падает;
- batch всё ещё завершается;
- `runtime_profile=deterministic` или effective backend возвращается к `local`;
- chat отвечает, но при OpenAI-сбое может перейти в degraded fallback.

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
- `runtime_metadata.generation_backend_effective`
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

Если на Railway включён OpenAI, для demo dataset и произвольного файла `runtime_profile` должен становиться `openai-enhanced`, а не оставаться deterministic-only.
