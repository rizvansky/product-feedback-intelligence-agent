# Tools & APIs

## Назначение

Документ задаёт единый контракт для внутренних tool call'ов и внешних provider integrations.

## Внутренние инструменты

| Tool | Вход | Выход | Timeout | Side effects |
|---|---|---|---|---|
| `search_clusters` | `query`, `filters`, `top_k` | список cluster hits | 1,5 c | нет |
| `get_quotes` | `cluster_id`, `limit` | цитаты | 1 c | нет |
| `get_trend` | `cluster_id`, `window_days` | trend snapshot | 1 c | нет |
| `compare_clusters` | `cluster_a`, `cluster_b` | diff summary | 1 c | нет |
| `get_report_section` | `section_name` | markdown fragment | 500 мс | нет |

Правила:

- все tool inputs валидируются Pydantic-схемой;
- tool errors типизируются и не пробрасываются как сырой stack trace;
- инструменты read-only и не меняют состояние session.

## Внешние adapter'ы

### Generation adapter

Контракт:

- вход: `messages`, `response_schema`, `temperature`, `timeout_s`, `budget_context`;
- выход: `text`, `structured_output`, `usage`, `provider`, `latency_ms`.

Политика:

- primary provider: OpenAI;
- fallback providers: Mistral, затем Anthropic;
- max 3 retries у primary и max 2 retries у каждого fallback provider;
- circuit breaker открывается после 5 последовательных provider errors.

### Embedding adapter

Контракт:

- вход: `texts[]`, `timeout_s`, `provider`;
- выход: `vectors[]`, `usage`, `provider`, `latency_ms`.

Политика:

- при частичном провале батч делится пополам и повторяется;
- при полном провале внешний backend отключается до истечения cooldown;
- локальный backend не требует внешнего ключа и используется как fallback.

## Error taxonomy

| Код | Смысл | Retryable |
|---|---|---|
| `VALIDATION_ERROR` | неверный входной контракт | нет |
| `TIMEOUT` | provider/tool timeout | да |
| `PROVIDER_UNAVAILABLE` | сеть, 5xx, rate limit | да |
| `CIRCUIT_OPEN` | адаптер временно заблокирован | нет |
| `BUDGET_EXCEEDED` | превышен cost cap | нет |
| `NO_EVIDENCE_AVAILABLE` | retriever не смог собрать grounded context | нет |

## Защита

- Raw PII запрещён в payload внешних provider'ов.
- Внешние вызовы обязаны логировать только метаданные, а не полные prompts.
- Для tool-use максимум 4 шага на один Q&A-запрос.
