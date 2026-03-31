# Serving & Config

## Назначение

Документ задаёт операционную конфигурацию PoC: как сервисы поднимаются, какие переменные обязательны и какие resource limits считаются валидными.

## Контейнеры

| Контейнер | Роль |
|---|---|
| `frontend` | Next.js UI |
| `api` | FastAPI serving layer |
| `worker` | orchestrator и batch-processing |
| `chroma` | vector store |

Дополнительно, без отдельного контейнера:

- SQLite на общем volume;
- local storage volume для артефактов.

## Обязательные конфиги

| Переменная | Назначение |
|---|---|
| `PFIA_ENV` | `dev` / `demo` |
| `PFIA_DATA_DIR` | корневая директория данных |
| `OPENAI_API_KEY` | ключ primary provider |
| `ANTHROPIC_API_KEY` | ключ fallback provider |
| `LLM_PRIMARY_MODEL` | основная generation model |
| `LLM_FALLBACK_MODEL` | резервная generation model |
| `EMBEDDING_PROVIDER` | `openai` / `local` |
| `MAX_BATCH_SIZE` | верхняя граница batch |
| `SESSION_RETENTION_DAYS` | срок хранения артефактов |
| `PROMETHEUS_ENABLED` | включение `/metrics` |
| `LANGSMITH_TRACING` | включение внешней трассировки |

## Версии моделей

Для каждой session конфигурация моделей фиксируется snapshot'ом:

- `generation_primary`;
- `generation_fallback`;
- `embedding_backend`;
- `clustering_profile`.

Это нужно, чтобы результат можно было объяснить и воспроизвести.

## Health endpoints

| Endpoint | Назначение |
|---|---|
| `/health/live` | процесс жив |
| `/health/ready` | зависимости готовы принимать трафик |
| `/metrics` | Prometheus-compatible metrics |

`ready` считается `false`, если:

- нет доступа к SQLite или data volume;
- worker не может писать checkpoint;
- primary и fallback generation providers одновременно недоступны дольше заданного окна.

## Ресурсные ограничения

| Компонент | CPU / RAM target |
|---|---|
| `frontend` | минимально, без жёстких вычислений |
| `api` | до 0,5 vCPU / 512 MB |
| `worker` | до 1 vCPU / 2 GB |
| `chroma` | до 0,5 vCPU / 1 GB |

## Секреты

- Ключи не коммитятся в репозиторий.
- `.env` используется только локально.
- В логах и trace metadata ключи маскируются.

## Операционные правила

- Один batch-job одновременно.
- Новые jobs не принимаются в `RUNNING`, если очередь уже заполнена.
- Chat доступен только для завершённых session.
