# Observability & Evals

## Назначение

Подсистема нужна для двух целей:

- видеть, что система стабильна и укладывается в SLO;
- ловить архитектурные регрессии до demo.

## Метрики

### Runtime

- `pfia_job_latency_seconds` по стадиям;
- `pfia_job_total` по статусам;
- `pfia_stage_retries_total`;
- `pfia_qna_latency_seconds`;
- `pfia_queue_depth`;
- `pfia_degraded_jobs_total`.

### Providers

- `pfia_llm_calls_total` по provider/model;
- `pfia_llm_errors_total` по error code;
- `pfia_embedding_calls_total`;
- `pfia_provider_circuit_open_total`.

### Budget / privacy

- `pfia_cost_usd_total`;
- `pfia_session_cost_usd`;
- `pfia_pii_quarantine_total`;
- `pfia_injection_detected_total`.

## Логи

Каждое событие должно иметь:

- `timestamp`;
- `level`;
- `session_id`;
- `job_id`;
- `stage`;
- `event`;
- `correlation_id`.

Обязательные события:

- start/end stage;
- provider call;
- retry;
- degraded mode activation;
- privacy gate result;
- report generated;
- Q&A answered / rejected.

## Трейсы

Ключевые span'ы:

- `upload.accepted`;
- `job.preprocess`;
- `job.embed`;
- `job.cluster`;
- `job.label`;
- `job.report`;
- `qna.retrieve`;
- `qna.generate`.

## Alerts

| Условие | Реакция |
|---|---|
| `degraded_jobs_total` растёт серией | проверить providers и budget caps |
| `pii_quarantine_total > 0` на demo наборе | блокирующая проверка до релиза |
| `job_latency_seconds p95 > 45s` | оптимизация batch sizes / provider timeouts |
| `provider_circuit_open_total > 0` | переключить demo на fallback profile |

## Evals и acceptance checks

Перед demo должны выполняться:

- smoke test полного batch-flow;
- regression test на PII masking;
- regression test на recovery после рестарта worker;
- offline eval retrieval на фиксированном наборе вопросов;
- проверка, что degraded mode явно виден в отчёте и API.

## Definition of done для infra-ready PoC

- есть dashboard или хотя бы экспортируемый набор метрик;
- есть воспроизводимый тест на recovery и fallback;
- есть явный лог и trace для каждого внешнего provider call;
- нет raw PII в логах и индексах.
