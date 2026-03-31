# Memory & Context

## Назначение

Модуль описывает, как PFIA хранит session state, собирает context для Q&A и ограничивает разрастание памяти и токенов.

## Уровни памяти

| Уровень | Содержимое | Где хранится | Политика |
|---|---|---|---|
| `session_state` | статус session, config snapshot, ссылки на артефакты | SQLite | persisted |
| `job_state` | stage, attempts, budget, provider health | SQLite | persisted |
| `artifact_memory` | sanitized JSONL, cluster snapshots, reports | local volume | persisted по retention |
| `chat_memory` | последние turns и summary истории | SQLite | session-scoped |
| `runtime_only` | временные буферы обработки | RAM | не сохраняется |

## Memory policy

- Нет cross-session memory.
- Нет user profiling.
- Raw input удаляется после прохождения privacy gate, если не включён явно режим отладки.
- Анонимизированные артефакты хранятся ограниченно и могут быть удалены вручную.

## Сборка контекста для Q&A

Контекст строится в таком порядке:

1. system instructions;
1. compact summary предыдущего диалога;
1. текущий вопрос пользователя;
1. `EvidenceBundle` из retrieval;
1. диагностические метки `degraded_mode`, если применимо.

## Context budget

| Элемент | Лимит |
|---|---|
| история чата | 3 явных turn'а или 1 summary |
| evidence fragments | до 12 |
| input tokens на generation | до 6 000 |
| output tokens | до 700 |

## Политика усечения

Если бюджет превышен:

1. удалить низкорелевантные quotes;
1. сократить историю в summary;
1. уменьшить число cluster hits;
1. вернуть controlled error, если бюджет всё ещё нарушен.

## Контрольные условия

- `chat_memory` не должен содержать raw PII.
- В память модели нельзя передавать данные вне текущей session.
- Каждый Q&A-ответ должен содержать ссылки на evidence, иначе response считается невалидным.
