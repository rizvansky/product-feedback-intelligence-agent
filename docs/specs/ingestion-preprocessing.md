# Ingestion & Preprocessing

## Назначение

Модуль принимает входной файл, приводит записи к единой схеме и гарантирует, что дальше по пайплайну и во внешние API идут только безопасные анонимизированные данные.

## Входной контракт

Поддерживаемые форматы:

- CSV;
- JSON;
- максимум 2 000 записей;
- максимум 10 MB на файл.

Минимальная логическая схема записи:

| Поле | Тип | Обязательность |
|---|---|---|
| `review_id` | string | да |
| `source` | enum | да |
| `text` | string | да |
| `created_at` | datetime | да |
| `rating` | int | нет |
| `language` | string | нет |
| `app_version` | string | нет |

## Выходной контракт

`ReviewNormalized`:

| Поле | Описание |
|---|---|
| `review_id` | исходный идентификатор записи |
| `session_id` | принадлежность сессии |
| `source` | App Store / Google Play / Zendesk / Telegram / NPS |
| `created_at` | нормализованная временная метка |
| `language` | `ru`, `en`, `mixed`, `unknown` |
| `text_normalized` | текст после нормализации Unicode, trim и cleanup |
| `text_anonymized` | текст после PII masking |
| `dedupe_hash` | SHA256 нормализованного текста |
| `flags` | `spam`, `pii_found`, `injection_suspected`, `low_information` |
| `metadata` | безопасные служебные поля |

Дополнительный артефакт: `PreprocessingSummary` с числами по дубликатам, quarantine, unsupported language и spam rate.

## Этапы обработки

1. Проверка формата файла и базовой схемы.
1. Нормализация кодировки и временных полей.
1. Удаление полных дубликатов по `dedupe_hash`.
1. Детекция языка.
1. PII masking через regex + spaCy NER.
1. Injection scan по сигнатурам и длине.
1. Эвристика low-information / spam.
1. Запись sanitized JSONL на локальный volume.

## Ограничения

- Если обязательное поле отсутствует более чем у 1 % записей, job завершается ошибкой.
- Если после PII scan остаются неподтверждённые потенциальные PII, записи уходят в quarantine.
- Если quarantine превышает 5 % batch, job переводится в `FAILED_PRIVACY`.
- Mixed-language записи допускаются, но маркируются флагом `mixed`.

## Latency budget

- p95 preprocessing для 1 000 отзывов: до 10 секунд.

## Ошибки

| Код | Условие | Поведение |
|---|---|---|
| `INPUT_SCHEMA_INVALID` | файл не проходит схему | fail-fast |
| `INPUT_LIMIT_EXCEEDED` | превышен batch/file size | fail-fast |
| `PRIVACY_GATE_FAILED` | слишком много записей в quarantine | job failed |
| `UNSUPPORTED_ENCODING` | файл невозможно корректно декодировать | job failed |

## Точки контроля

- Нельзя запускать embeddings до завершения privacy gate.
- Raw input не должен попадать в SQLite, Chroma и логи в открытом виде.
- Sanitized JSONL должен быть воспроизводимым: повторный прогон даёт тот же `dedupe_hash`.
