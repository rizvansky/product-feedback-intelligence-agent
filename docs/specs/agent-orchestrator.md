# Agent & Orchestrator

## Назначение

Оркестратор управляет жизненным циклом batch-job и Q&A, обеспечивает retries, fallback, идемпотентность стадий и перевод системы в корректный degraded mode вместо хаотичного отказа.

## Модель исполнения

- Один worker обрабатывает один batch-job за раз.
- Источник истины по состоянию: SQLite.
- Текущий runtime использует LangGraph поверх сервисных stage-функций, но persisted state и переходы всё равно задаются отдельной SQLite-backed state machine, а не только runtime-графом.

## Статусы job

| Статус | Смысл |
|---|---|
| `QUEUED` | job принят, ждёт worker |
| `RUNNING` | выполняется текущая стадия |
| `RETRYING` | текущая стадия повторяется |
| `DEGRADED_RUNNING` | пайплайн идёт в degraded mode |
| `COMPLETED` | успешное завершение |
| `DEGRADED_COMPLETED` | завершение с пониженным качеством |
| `FAILED_*` | финальная ошибка по типу |
| `CANCELED` | прерван пользователем |

## Стадии

1. `VALIDATE_INPUT`
1. `PREPROCESS`
1. `EMBED`
1. `CLUSTER`
1. `LABEL_AND_SUMMARIZE`
1. `SCORE`
1. `DETECT_ANOMALIES`
1. `INDEX_FOR_RETRIEVAL`
1. `BUILD_REPORT`
1. `FINALIZE`

## Правила переходов

- Между стадиями обязательно сохраняется checkpoint.
- Retry выполняется только для retryable ошибок.
- Если стадия перешла в degraded mode, это состояние тянется дальше как часть job metadata.
- `FINALIZE` возможен только если report сохранён и retrieval-индекс либо собран, либо помечен как degraded.

## Retry policy

| Стадия | Попытки | Примечание |
|---|---|---|
| `PREPROCESS` | 1 | ошибки здесь обычно не retryable |
| `EMBED` | 3 | provider retry + fallback backend |
| `CLUSTER` | 3 | профили параметров, не простое повторение |
| `LABEL_AND_SUMMARIZE` | 3 | primary, затем fallback provider |
| `BUILD_REPORT` | 2 | storage / template retry |

## Stop conditions

Job останавливается немедленно, если:

- нарушен privacy gate;
- storage недоступен после лимита retries;
- не соблюдён минимальный входной контракт;
- пользователь отменил session.

## Идемпотентность

- Повторная обработка стадии с тем же `job_id` не должна дублировать артефакты.
- Все артефакты именуются по `session_id` и `stage`.
- Tool calls в Q&A не должны изменять состояние.

## Восстановление после рестарта

- Worker при старте сканирует jobs в статусах `RUNNING`, `RETRYING`, `DEGRADED_RUNNING`.
- Если checkpoint существует, job возобновляется с последней незавершённой стадии.
- Если checkpoint повреждён, job переводится в `FAILED_RECOVERY`.
