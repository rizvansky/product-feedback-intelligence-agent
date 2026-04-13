# Railway Deploy Runbook

Этот runbook описывает рекомендуемый hosted deploy для текущего PoC: один Railway web service + один persistent volume. Для этого проекта это лучший operational tradeoff, потому что runtime state хранится в SQLite, report artifacts и retrieval index на локальном диске.

## Целевой Профиль

- один сервис `pfia-web`
- один Railway volume, смонтированный в `/data`
- одна реплика
- встроенный background worker внутри web process
- platform domain Railway на первом этапе

Почему именно так:

- `SQLite + local artifacts` плохо сочетаются с разнесением `api` и `worker` по разным hosted services без общего диска
- один сервис даёт самый быстрый путь к публичному URL
- `railway.json` уже зафиксировал безопасные deploy defaults

## Что Уже Подготовлено В Репозитории

- `Dockerfile` для production-style container build
- `railway.json` с:
  - `DOCKERFILE` builder
  - `startCommand=pfia-api --host 0.0.0.0`
  - `healthcheckPath=/health/ready`
  - `numReplicas=1`
  - `requiredMountPath=/data`
- platform-aware config:
  - `PORT` подхватывается автоматически
  - `RAILWAY_VOLUME_MOUNT_PATH` автоматически переводит runtime state в persistent volume
  - наличие Railway volume автоматически включает embedded worker mode

## Порядок Деплоя

1. Запушить актуальную ветку в GitHub.
1. В Railway создать новый проект из этого репозитория.
1. Убедиться, что сервис собрался из `Dockerfile`.
1. Добавить volume к этому сервису и смонтировать его в `/data`.
1. Запустить деплой.
1. После успешного старта сгенерировать public domain.

На минимальном PoC-профиле дополнительные env vars не обязательны.

## Рекомендуемые Переменные

Базовый deploy может работать и без них, но полезно явно зафиксировать:

- `PFIA_ENV=prod`
- `PFIA_LOG_LEVEL=INFO`
- `PFIA_MAX_QUEUE_DEPTH=3`
- `PFIA_MAX_BATCH_SIZE=2000`

Опционально:

- `OPENAI_API_KEY`, если позже понадобится реальная внешняя генерация
- `OPENAI_BASE_URL`, если будет прокси или совместимый endpoint

Не нужно вручную задавать:

- `PFIA_PORT`: Railway сам передаст `PORT`
- `PFIA_DATA_DIR`: при наличии volume путь будет вычислен автоматически
- `PFIA_EMBEDDED_WORKER`: при наличии Railway volume режим включится автоматически

## Что Проверить После Деплоя

Открыть:

- `/health/live`
- `/health/ready`
- `/metrics`
- `/`

Ожидаемое состояние:

- `/health/live` возвращает `200`
- `/health/ready` возвращает `200`
- в `/health/ready` видно `worker.mode=embedded`
- `storage.data_dir` указывает на `/data/runtime`

Затем проверить пользовательский flow:

1. Открыть UI.
1. Нажать `Run Demo Dataset`.
1. Дождаться статуса `COMPLETED`.
1. Проверить, что report и clusters отрисовались.
1. В Q&A спросить: `What is the highest-priority issue and what evidence supports it?`

## Ограничения Текущего Hosted Профиля

- не масштабировать выше `1` реплики
- не выносить API и worker в отдельные сервисы, пока state живёт в SQLite и локальных файлах
- не рассчитывать на zero-downtime redeploy при attached volume

## Следующий Шаг После Первого Деплоя

Когда этот профиль будет стабильно работать, можно переходить к следующему уровню:

- custom domain
- managed Postgres вместо SQLite
- object storage вместо локальных артефактов
- разнос `api` и `worker` на отдельные hosted services
- внешний LLM backend с секретами Railway
