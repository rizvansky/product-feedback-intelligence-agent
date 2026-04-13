# Retriever

> Примечание по текущему runtime: в коде репозитория retriever реализован как persisted on-disk session index с TF-IDF + cosine similarity + keyword overlap. Этот spec сохраняет target vocabulary (`reviews_vector`, `clusters_vector`, `Chroma`) как более широкий design-level reference, но фактический PoC сейчас не поднимает отдельный Chroma service.

## Назначение

Retriever обслуживает Q&A поверх уже построенных артефактов сессии и отвечает за стабильное получение grounded evidence даже при деградации части индексов.

## Источники данных

- `clusters` с label и summary;
- `quotes` по кластерам;
- `reviews` в анонимизированном виде;
- `trend snapshots`;
- `priority scores`.

## Индексы

| Индекс | Назначение | Хранилище |
|---|---|---|
| `reviews_vector` | semantic recall на уровне отзывов | Chroma |
| `clusters_vector` | semantic recall на уровне тем | Chroma |
| `cluster_fts` | keyword fallback по label / summary / quotes | SQLite FTS |

## Поисковый pipeline

1. Применить обязательный фильтр `session_id`.
1. Применить пользовательские metadata filters, если они есть.
1. Выполнить semantic search по `clusters_vector`.
1. Выполнить semantic search по `reviews_vector`.
1. Выполнить keyword search по `cluster_fts`.
1. Слить выдачу через deterministic rank fusion.
1. Добрать цитаты и тренд по top cluster hit'ам.
1. Сформировать `EvidenceBundle`.

## Контракт поиска

`search_clusters(query, filters, top_k)` возвращает:

| Поле | Описание |
|---|---|
| `cluster_id` | идентификатор темы |
| `score` | итоговый fused score |
| `match_reason` | semantic / keyword / hybrid |
| `label` | название темы |
| `summary` | краткое описание темы |
| `priority_score` | приоритет темы |

`EvidenceBundle` содержит:

- до 5 cluster hits;
- до 6 цитат;
- до 2 trend snippets;
- estimate контекстных токенов.

## Ограничения

- p95 retrieval latency: до 1,5 секунды.
- Если semantic index недоступен, retriever обязан продолжить работу через keyword-only mode.
- Если отсутствуют и vector, и keyword индексы, Q&A возвращает `NO_EVIDENCE_AVAILABLE` и не вызывает LLM.

## Fallback-режимы

| Режим | Когда включается | Что теряется |
|---|---|---|
| `hybrid` | нормальный режим | ничего |
| `keyword_only` | нет embeddings или Chroma | падает semantic recall |
| `summary_only` | нет review-level index | меньше цитат, хуже детализация |

## Guardrails

- Retrieval никогда не выходит за рамки одной session.
- Quotes должны возвращаться только в анонимизированном виде.
- `top_k` ограничен сверху значением 10.
