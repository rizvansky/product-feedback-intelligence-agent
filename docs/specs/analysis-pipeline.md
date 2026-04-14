# Analysis Pipeline

## Назначение

Модуль превращает массив `ReviewNormalized[]` в кластеры, приоритеты, аномалии и итоговые артефакты для отчёта и Q&A.

## Подмодули

- `embedding-runner`;
- `preprocess-review-agent`;
- `clustering-engine`;
- `cluster-review-agent`;
- `label-summary-generator`;
- `priority-scorer`;
- `anomaly-detector`;
- `anomaly-explainer-agent`;
- `report-artifact-builder`.

## Входной контракт

- `ReviewNormalized[]`;
- config snapshot текущей session;
- исторические агрегаты по прошлым session, если доступны;
- budget counters и provider status.

## Выходной контракт

`ClusterRecord`:

| Поле | Описание |
|---|---|
| `cluster_id` | идентификатор кластера |
| `label` | human-readable тема |
| `summary` | краткое объяснение темы |
| `review_ids` | состав кластера |
| `top_quote_ids` | опорные цитаты |
| `priority_score` | число от 0 до 1 |
| `sentiment_score` | агрегированный sentiment |
| `trend_delta` | изменение относительно baseline |
| `confidence` | `high`, `medium`, `low` |
| `degraded_reason` | причина деградации, если есть |

Дополнительно:

- `AlertRecord[]`;
- `ReportArtifact`;
- `ClusterMetricsSnapshot`.

## Embedding policy

- Primary backend: OpenAI embeddings.
- Fallback backend: локальная sentence-transformer модель.
- Batch size для внешнего провайдера: 128 текстов.
- Если оба backend недоступны, система помечает session как `degraded_retrieval_only`, но пайплайн может продолжить scoring и report по детерминированным агрегатам.

## Clustering policy

Базовый профиль:

- `algorithm = HDBSCAN`;
- `min_cluster_size = 5`;
- `min_samples = 2`;
- noise points сохраняются отдельно.

Правила:

- Если silhouette `< 0.35`, оркестратор пробует до 3 профилей.
- Diagnostic trace сохраняет:
  - выбранный backend кластеризации;
  - выбранный профиль;
  - число попыток reflection loop;
  - quality gate result;
  - cluster-count gate result.
- Если после всех попыток quality gate не пройден, включается weak-signals mode:
  - малые кластеры сохраняются;
  - label и summary помечаются `low_confidence`;
  - отчёт содержит предупреждение.

## Labeling и summary

- На LLM уходят только анонимизированные примеры.
- Выход должен соответствовать typed JSON schema.
- При провале generation допускается deterministic fallback:
  - label на базе top keywords;
  - summary как шаблон из частоты, тональности и источников.

## Hybrid preprocessing review

- Regex и простые эвристики остаются first-pass фильтрами.
- При наличии локально установленных spaCy-моделей privacy stage дополнительно маскирует person entities через `ru_core_news_sm` / `en_core_web_sm`.
- Если доступен OpenAI runtime, `PreprocessReviewAgent` делает second-pass review для пограничных случаев:
  - `spam`;
  - `injection_suspected`;
  - `low_information`.
- При недоступности OpenAI базовые heuristic flags сохраняются без hard failure.

## Scoring

- Primary sentiment backend: `VADER`.
- Fallback sentiment backend: deterministic lexical scorer для `ru` и для сред без `vaderSentiment`.
- Runtime metadata фиксирует effective sentiment backend и model.

Формула PoC:

```text
priority_score =
    0.5 * freq_norm +
    0.3 * abs(sentiment_score) +
    0.2 * trend_delta_norm
```

Свойства:

- score детерминирован и воспроизводим;
- веса задаются конфигом;
- расчёт не требует LLM.

## Anomaly detection

- Базовое правило: `mean + 2 * std` на rolling window.
- Если исторических данных нет, модуль возвращает `insufficient_history` вместо hard failure.
- Severity вычисляется детерминированно на основе величины отклонения.
- Если доступен OpenAI runtime, `AnomalyExplainerAgent` переписывает детерминированные alert reasons в grounded PM-friendly explanations.

## Cluster review

- Детерминированная кластеризация остаётся базовым источником групп.
- Если доступен OpenAI runtime, `ClusterReviewAgent` может:
  - предложить safe merge близких кластеров;
  - пометить широкие кластеры как кандидаты на split review.
- Merge/split review не заменяет сам deterministic clustering engine, а дополняет его semantic layer.

## Report artifact

Отчёт должен содержать:

- executive summary;
- top clusters;
- top quotes per cluster;
- weak signals section;
- simple list view section when `low_data_mode=true`;
- alerts section;
- note о degraded mode, если он был активирован.

## Ошибки и fallback

| Код | Поведение |
|---|---|
| `EMBED_PROVIDER_UNAVAILABLE` | fallback на локальные embeddings |
| `CLUSTER_QUALITY_LOW` | retry профиля, затем weak-signals mode |
| `LABEL_GENERATION_FAILED` | template labels / summaries |
| `ANOMALY_HISTORY_MISSING` | пометка `insufficient_history`, job продолжается |
| `REPORT_BUILD_FAILED` | stage retry, затем job failed |
