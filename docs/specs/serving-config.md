# Serving & Config

## Назначение

Документ задаёт операционную конфигурацию PoC: как сервисы поднимаются, какие переменные обязательны и какие resource limits считаются валидными.

## Контейнеры

Локальный PoC-профиль использует два runtime-сервиса:

| Контейнер | Роль |
|---|---|
| `api` | FastAPI serving layer + встроенный статический frontend |
| `worker` | orchestrator и batch-processing |

Дополнительно, без отдельного контейнера:

- SQLite на общем volume;
- local storage volume для upload-файлов, sanitized artifacts, reports и retrieval index;
- persisted on-disk retrieval index вместо отдельного Chroma-контейнера.

Hosted deploy profile для Railway упрощён до одного web-service:

- `api` остаётся FastAPI serving layer;
- `worker` поднимается как embedded background thread внутри того же процесса;
- runtime state уходит в Railway volume через `RAILWAY_VOLUME_MOUNT_PATH`.

## Обязательные конфиги

| Переменная | Назначение |
|---|---|
| `PFIA_ENV` | `dev` / `demo` / `prod` |
| `PFIA_DATA_DIR` | корневая директория данных |
| `PFIA_EMBEDDED_WORKER` | принудительно включает single-service mode |
| `PFIA_PORT` / `PORT` | HTTP port |
| `OPENAI_API_KEY` | опциональный ключ внешнего provider, если он будет использоваться |
| `OPENAI_BASE_URL` | base URL внешнего provider |
| `PFIA_LLM_PRIMARY_MODEL` | основная generation model |
| `PFIA_LLM_FALLBACK_MODEL` | резервная generation model |
| `PFIA_EMBEDDING_BACKEND` | `local` / `openai` |
| `PFIA_MAX_BATCH_SIZE` | верхняя граница batch |
| `PFIA_SESSION_RETENTION_DAYS` | срок хранения артефактов |
| `PFIA_PROMETHEUS_ENABLED` | включение `/metrics` |

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
- worker не может обновлять heartbeat;
- heartbeat от worker протух дольше заданного окна.

## Ресурсные ограничения

| Компонент | CPU / RAM target |
|---|---|
| `api` | до 0,5 vCPU / 512 MB |
| `worker` | до 1 vCPU / 2 GB |

Для Railway-hosted single-service режима целевой sizing проще:

| Компонент | CPU / RAM target |
|---|---|
| `pfia-web` | от 1 vCPU / 1 GB |

## Секреты

- Ключи не коммитятся в репозиторий.
- `.env` используется только локально.
- В логах и trace metadata ключи маскируются.

## Операционные правила

- Один batch-job одновременно.
- Новые jobs не принимаются в `RUNNING`, если очередь уже заполнена.
- Chat доступен только для завершённых session.
- Hosted deploy фиксируется в `1` реплику, пока state основан на SQLite и локальных файлах.
