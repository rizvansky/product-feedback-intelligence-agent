# System Design

______________________________________________________________________

## 1. Назначение документа

Этот документ фиксирует целевую архитектуру PoC-системы Product Feedback Intelligence Agent (PFIA). Фокус выбран под инфраструктурный трек: система должна быть предсказуемой, наблюдаемой, устойчивой к деградациям LLM/API и укладываться в жёсткие ресурсные ограничения PoC.

Область охвата документа:

- состав модулей и границы ответственности;
- runtime-взаимодействие компонентов;
- execution flow для batch-обработки и Q&A;
- state / memory / context handling;
- retrieval-контур;
- tool/API-интеграции;
- failure modes, fallback, guardrails;
- технические и операционные ограничения;
- точки контроля, которые должны быть реализованы до перехода к demo-ready стадии.

Примечание по текущему репозиторию: этот документ фиксирует целевой design и логические роли компонентов. Текущая PoC-реализация в коде использует упрощённую runtime-топологию: статический frontend обслуживается FastAPI-приложением, а retrieval index хранится как persisted on-disk artifact вместо отдельного внешнего vector DB сервиса.

______________________________________________________________________

## 2. Архитектурные драйверы

### 2.1 Что оптимизируем

Для PoC приоритеты архитектуры расположены так:

1. **Надёжность batch-обработки**: система должна завершать типовой прогон без ручного вмешательства даже при частичной деградации внешних провайдеров.
1. **Предсказуемость исполнения**: каждое состояние джобы, стоимость и причина деградации должны быть явно зафиксированы.
1. **Устойчивость к недоступности LLM/API**: внешние вызовы не должны быть единственной точкой отказа.
1. **Контроль ресурсов**: PoC работает на одной машине с ограничениями по CPU, RAM и бюджету на API.
1. **Достаточность для дальнейшей реализации**: дизайн должен уже сейчас задавать контракты модулей и критерии готовности.

### 2.2 Ключевые решения

| Решение | Почему принято |
|---|---|
| Batch-обработка выполняется асинхронным `job`, а не внутри HTTP-запроса | Исключает таймауты API и позволяет переживать рестарты через persisted state |
| Оркестрация строится как явная state machine с сохранением checkpoint'ов | Для PoC это надёжнее, чем держать критическое состояние только в памяти графа |
| Источник истины по состоянию джоба хранится в SQLite (WAL) | Дешевле и проще Postgres для single-node PoC, при этом даёт персистентность и базовую конкуренцию чтения/записи |
| Chroma используется в persistent-режиме, а не только in-memory | Q&A и повторные запросы должны переживать рестарт контейнера |
| Все LLM и embedding вызовы идут через provider adapters + circuit breaker | Упрощает fallback, наблюдаемость и политику retries |
| До обращения к LLM выполняется детерминированный preprocessing | Минимизирует утечки PII, снижает стоимость и ограничивает blast radius внешних сбоёв |
| Retrieval делается гибридным: vector search + keyword fallback | Система не должна полностью терять поиск при проблемах с embedding-провайдером |
| Raw данные хранятся только краткоживуще, анонимизированные артефакты отдельно | Это согласует требования приватности с потребностью в повторном Q&A |
| Наблюдаемость встроена в пайплайн как обязательный слой, а не post factum | Для инфраструктурного трека важна не только функциональность, но и управляемость системы |

______________________________________________________________________

## 3. Целевая топология

PFIA реализуется как single-node система в Docker Compose со следующими контейнерами и логическими слоями:

- **Frontend**: Next.js UI для upload, статуса job, просмотра отчёта и Q&A.
- **API / Serving**: FastAPI для внешнего HTTP-контракта, приёма файлов, выдачи статуса и ответов.
- **Worker / Orchestrator**: Python-процесс, который забирает queued jobs и проходит stage-by-stage state machine.
- **Storage Layer**:
  - SQLite для метаданных, статусов, checkpoint'ов и audit trail;
  - ChromaDB для embeddings и retrieval-индексов;
  - локальный volume для upload-файлов, анонимизированных артефактов и сгенерированных Markdown-отчётов.
- **Provider Layer**:
  - OpenAI как primary provider для embeddings и генерации;
  - Anthropic Claude Haiku как fallback для generation;
  - локальная sentence-transformer модель как fallback для embeddings;
  - spaCy и regex как локальные детерминированные зависимости.
- **Observability Layer**:
  - structured JSON logs;
  - Prometheus-compatible metrics endpoint;
  - OpenTelemetry traces;
  - LangSmith как опциональный sink для LLM-трассировки.

______________________________________________________________________

## 4. Модули и их роли

| Модуль | Роль | Критичные входы | Критичные выходы |
|---|---|---|---|
| `frontend` | Upload, статус, просмотр отчёта, Q&A UI | Файл, вопрос пользователя | HTTP-запросы к API |
| `api-gateway` | Валидация внешних запросов, создание job, отдача артефактов | multipart upload, query params, chat request | `job_id`, `session_id`, report/chat responses |
| `job-orchestrator` | State machine, retries, fallback, checkpointing | `job_id`, config snapshot | stage status, failure reason, итоговые артефакты |
| `ingestion-preprocessing` | Парсинг, schema validation, dedupe, PII scrub, language detection, spam/injection scan | CSV/JSON export | `ReviewNormalized[]`, preprocessing summary |
| `analysis-pipeline` | Embeddings, clustering, labeling, summary, scoring, anomaly detection | анонимизированные тексты | `ClusterRecord[]`, alerts, report sections |
| `retriever` | Индексация и поиск evidence для Q&A | clusters, quotes, trend data, user query | `EvidenceBundle` |
| `tool-layer` | Единый контракт инструментов для Q&A-агента | tool call | tool result / typed error |
| `report-builder` | Сборка Markdown-отчёта и executive summary | clusters, scores, alerts | `report.md`, executive summary |
| `metadata-store` | Persisted state и аудит | job/session events | queryable state |
| `observability` | Метрики, логи, трейсы, cost tracking | runtime events | dashboards, alerts, trace graph |

______________________________________________________________________

## 5. Основные сущности и контракты

| Сущность | Назначение | Минимальные поля |
|---|---|---|
| `Session` | Логическая единица работы с одним загруженным датасетом | `session_id`, `created_at`, `status`, `config_snapshot`, `retention_mode` |
| `Job` | Асинхронный прогон пайплайна | `job_id`, `session_id`, `stage`, `attempt`, `status`, `cost_usd`, `failure_code` |
| `ReviewNormalized` | Нормализованный анонимизированный отзыв | `review_id`, `source`, `created_at`, `language`, `text_anonymized`, `dedupe_hash`, `flags` |
| `ClusterRecord` | Семантический кластер с объяснимыми атрибутами | `cluster_id`, `label`, `summary`, `review_ids`, `priority_score`, `confidence`, `top_quote_ids` |
| `AlertRecord` | Аномалия или системное предупреждение | `alert_id`, `type`, `severity`, `cluster_id`, `reason`, `timestamp` |
| `EvidenceBundle` | Контекст для ответа в Q&A | `query`, `cluster_hits`, `quotes`, `trends`, `context_tokens_estimate` |
| `ReportArtifact` | Итоговый отчёт и его метаданные | `report_id`, `session_id`, `path`, `generated_at`, `degraded_mode` |

Контракты должны быть typed и versioned на уровне Python-моделей, чтобы при реализации их можно было использовать и как internal DTO, и как API schema.

______________________________________________________________________

## 6. Execution Flow

### 6.1 Batch-flow: от upload до отчёта

1. Пользователь загружает CSV/JSON через frontend.
1. `api-gateway` создаёт `session` и `job`, сохраняет upload-файл во временный volume и переводит job в `QUEUED`.
1. `job-orchestrator` забирает job и запускает `VALIDATE_INPUT`.
1. После успешной валидации выполняется `PREPROCESS`:
   - нормализация схемы;
   - дедупликация;
   - PII anonymization;
   - language detection;
   - spam / injection scan.
1. Если preprocessing прошёл контрольные условия, запускается `EMBED`.
1. После получения embeddings выполняется `CLUSTER`.
1. Если качество кластеризации ниже порога, оркестратор повторяет `CLUSTER` с альтернативным профилем параметров; после исчерпания лимита переводит job в `DEGRADED`, но не обязательно в `FAILED`.
1. Затем идут `LABEL_AND_SUMMARIZE`, `SCORE`, `DETECT_ANOMALIES`.
1. `retriever` индексирует кластеры и цитаты.
1. `report-builder` генерирует Markdown-отчёт и executive summary.
1. Оркестратор фиксирует `COMPLETED` или `DEGRADED_COMPLETED`, публикует ссылки на артефакты и метрики исполнения.

### 6.2 Q&A-flow: после завершения batch-job

1. Пользователь задаёт вопрос к готовой сессии.
1. `api-gateway` проверяет, что session находится в `COMPLETED` или `DEGRADED_COMPLETED`.
1. `tool-layer` и `retriever` собирают evidence:
   - релевантные кластеры;
   - цитаты;
   - тренды;
   - метаданные фильтрации.
1. Если evidence недостаточен, агент может сделать ещё один cycle tool use, но в пределах лимита шагов.
1. Ответ собирается только на базе evidence bundle.
1. Перед выдачей выполняется проверка на пустую аргументацию, превышение context budget и повторный PII scan.
1. В ответе обязательно возвращаются цитаты-основания и идентификаторы кластеров.

______________________________________________________________________

## 7. State / Memory / Context Handling

### 7.1 Слои состояния

| Слой | Где хранится | Назначение | Время жизни |
|---|---|---|---|
| Runtime state job | SQLite | stage, attempt, retries, timers, budget, provider status | до удаления session |
| Raw upload | локальный volume | входной файл для повторного чтения в рамках текущего job | краткоживущий, по умолчанию удаляется после успешного preprocessing |
| Sanitized artifacts | локальный volume + SQLite metadata | анонимизированные JSONL, cluster snapshots, report | по политике retention, по умолчанию 7 дней для PoC |
| Retrieval index | Chroma persistent volume | Q&A по завершённому batch | пока жива session |
| Chat session memory | SQLite + compact summary | последние turns и summary истории | пока жива session |

### 7.2 Правила памяти

- Система **не использует глобальную долгоживущую memory** между разными dataset/session.
- Raw отзывы **не попадают** в retrieval index и не отправляются во внешние LLM.
- В chat-контекст попадают:
  - пользовательский вопрос;
  - summary последних turn'ов;
  - evidence bundle из retrieval;
  - системные инструкции для grounded answer.
- При превышении context budget применяется жёсткая политика сокращения:
  1. удалить низкорелевантные цитаты;
  1. сократить историю чата до summary;
  1. уменьшить число кластеров в evidence;
  1. если бюджет всё ещё превышен, вернуть controlled error `CONTEXT_BUDGET_EXCEEDED`.

### 7.3 Context budget

| Контекст | Лимит |
|---|---|
| Evidence bundle для Q&A | до 12 фрагментов |
| Суммарный контекст на generation | до 6 000 input tokens |
| История диалога в явном виде | до 3 последних turn'ов |
| Максимум tool-use шагов в одном ответе | 4 |

______________________________________________________________________

## 8. Retrieval-контур

Retrieval нужен не для общего open-domain поиска, а для grounded-answering поверх уже обработанного набора отзывов. Поэтому контур строится вокруг session-scoped индексов.

### 8.1 Источники retrieval

- summaries и labels кластеров;
- embeddings отдельных отзывов;
- отобранные цитаты по кластеру;
- агрегаты по динамике и приоритету;
- metadata-фильтры: `source`, `language`, `app_version`, `date_range`.

### 8.2 Индексы

- `chroma.reviews`: embedding на уровне анонимизированного отзыва;
- `chroma.clusters`: embedding на уровне summary кластера;
- `sqlite_fts.cluster_text`: keyword index по label, summary и top quotes.

### 8.3 Поисковый pipeline

1. Фильтрация по `session_id` и явным metadata constraints.
1. Semantic search по `clusters` и `reviews`.
1. Keyword fallback/search по SQLite FTS.
1. Слияние результатов через deterministic fusion.
1. Расширение top cluster hit'ов связанными цитатами и трендом.
1. Формирование `EvidenceBundle`.

### 8.4 Почему retrieval сделан гибридным

- semantic-only поиск хрупок при недоступности embedding-провайдера;
- keyword-only поиск теряет смысловые связи;
- гибридный контур даёт стабильный degraded mode без полной потери функциональности.

______________________________________________________________________

## 9. Tool и API интеграции

### 9.1 Внутренние инструменты Q&A-агента

| Tool | Назначение | Side effects |
|---|---|---|
| `search_clusters(query, filters, top_k)` | Найти релевантные кластеры | Нет |
| `get_quotes(cluster_id, limit)` | Вернуть анонимизированные цитаты | Нет |
| `get_trend(cluster_id, window_days)` | Вернуть динамику по кластеру | Нет |
| `compare_clusters(cluster_a, cluster_b)` | Сравнить две темы по частоте, тональности и тренду | Нет |
| `get_report_section(section_name)` | Взять уже сгенерированный фрагмент отчёта | Нет |

### 9.2 Внешние интеграции

| Интеграция | Роль | Timeout | Retry | Fallback |
|---|---|---|---|---|
| OpenAI Embeddings | Primary embeddings | 20 c | 3 попытки, exponential backoff | локальная embedding-модель, затем lexical-only retrieval |
| OpenAI Generation | Primary labeling / summary / Q&A | 25 c | 3 попытки | Anthropic Haiku, затем template/degraded mode |
| Anthropic Generation | Secondary generation provider | 25 c | 2 попытки | template/degraded mode |
| LangSmith / OTLP sink | Трассировка и диагностика | 5 c | 1 попытка | silent drop, чтобы observability не блокировала pipeline |

### 9.3 Правила вызова внешних API

- Каждому вызову присваивается `call_id` и `correlation_id`.
- Перед внешним вызовом проверяются:
  - budget gate;
  - circuit breaker status;
  - запрет на raw PII;
  - допустимый размер payload.
- Результат каждого вызова логируется как metadata, без хранения полного пользовательского контента.

______________________________________________________________________

## 10. Failure Modes, Fallback и Guardrails

| Сбой / риск | Что делает система |
|---|---|
| Некорректный CSV/JSON | Job завершается в `FAILED_INPUT`, пользователю возвращается schema error с указанием поля |
| Batch > 2 000 отзывов | Либо reject, либо controlled chunking по 500 записей; для PoC выбран reject c явным сообщением |
| Неудалённый PII после preprocessing | Записи уходят в quarantine; если доля превышает порог, job останавливается |
| Недоступен embedding provider | Переключение на локальный embedding backend; если он недоступен, retrieval переходит в keyword-only degraded mode |
| Недоступен LLM provider | Retry, затем fallback на secondary provider; при повторном сбое отчёт формируется в deterministic degraded mode без narrative summary |
| Слишком низкое качество кластеризации | До 3 попыток с альтернативными профилями параметров; затем weak-signals mode и явная пометка `low_confidence` |
| Нет исторических данных для anomaly detection | Раздел аномалий помечается как `insufficient_history`, job не падает |
| Превышен cost budget | Оркестратор прекращает необязательные LLM-этапы и завершает job в degraded mode |
| Ошибка при записи в storage | Stage retry; если ошибка повторяется, job останавливается как `FAILED_PERSISTENCE` |
| Отказ observability backend | Основной pipeline продолжает работать, telemetry дропается без влияния на пользователя |
| Prompt injection в отзыве | Отзыв quarantine, событие логируется, текст не используется как инструкция |

### 10.1 Guardrails

- PII scrub обязателен перед любым внешним LLM-вызовом.
- Q&A никогда не отвечает без evidence bundle.
- Каждая стадия должна быть идемпотентной относительно `job_id + stage + attempt`.
- Cost cap и latency budget проверяются до запуска дорогих стадий.
- Все degraded режимы должны быть явно отражены в отчёте и API response metadata.

______________________________________________________________________

## 11. Ограничения и SLO

### 11.1 Технические ограничения

| Ограничение | Значение |
|---|---|
| Deployment model | single-node Docker Compose |
| Ресурсы хоста | 2 vCPU, 4 GB RAM |
| Максимальный batch | 2 000 отзывов |
| Поддерживаемые языки | RU, EN |
| Типы входных файлов | CSV, JSON |
| Максимальный размер upload | 10 MB |
| Активные batch jobs | 1 одновременно |
| Очередь jobs | до 3 ожидающих jobs |

### 11.2 Операционные ограничения

| Ограничение | Цель |
|---|---|
| Стоимость одного batch на 1 000 отзывов | \<= 0,40 USD |
| Совокупный бюджет разработки и demo | \<= 10 USD |
| p95 batch latency на 1 000 отзывов | \<= 45 c |
| Hard timeout batch в demo | 60 c |
| p95 latency Q&A | \<= 8 c |
| Hard timeout Q&A | 12 c |
| Доля завершённых batch jobs на чистом тестовом наборе | >= 90 % |
| Raw PII leakage во внешние API | 0 % |

### 11.3 Надёжность для PoC

Для PoC фиксируется не production-grade SLA, а engineering target:

- система должна восстанавливаться после рестарта без потери завершённых session;
- in-flight job может быть повторно запущен с последнего checkpoint;
- отсутствие вторичного LLM-провайдера не должно ломать ingestion, preprocessing, scoring и anomaly detection;
- observability не должна быть blocking dependency.

______________________________________________________________________

## 12. Точки контроля

Перед переходом к реализации и далее перед demo-ready сборкой должны существовать следующие контрольные точки:

| Контрольная точка | Что проверяется |
|---|---|
| `CP-01 Input Gate` | схема файла, размер batch, обязательные поля |
| `CP-02 Privacy Gate` | PII scrub завершён, raw текст не уходит во внешние API |
| `CP-03 Budget Gate` | прогнозируемая стоимость в рамках лимитов |
| `CP-04 Provider Gate` | primary/fallback provider health и circuit breaker status |
| `CP-05 Quality Gate` | silhouette threshold, weak-signal policy, валидность cluster artifacts |
| `CP-06 Retrieval Gate` | индекс построен, evidence bundle собирается в пределах latency budget |
| `CP-07 Report Gate` | отчёт содержит executive summary, top clusters, alerts/degraded note |
| `CP-08 Observability Gate` | есть trace id, metrics и structured logs по всем критичным стадиям |

______________________________________________________________________

## 13. Что считается достаточным для начала реализации

Архитектурный пробел считается закрытым, если к реализации готовы:

- state machine и статусы job;
- contracts для `ReviewNormalized`, `ClusterRecord`, `EvidenceBundle`, `ReportArtifact`;
- provider adapter layer;
- storage layout и retention policy;
- список degraded modes и stop conditions;
- список telemetry сигналов и acceptance checks.

Детализация этих частей вынесена в [спецификации модулей](./specs/README.md) и [диаграммы](./diagrams/README.md).
