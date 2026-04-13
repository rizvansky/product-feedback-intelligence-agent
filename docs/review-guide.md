# Async Review Guide

Этот файл нужен для асинхронной проверки проекта ментором или преподавателем.

## Links

- Repository: `https://github.com/rizvansky/product-feedback-intelligence-agent`
- Live demo: `https://product-feedback-intelligence-agent-production.up.railway.app`

Публичный деплой проверен 13 апреля 2026 года.

## What Is Deployed

На Railway задеплоен текущий PoC-профиль проекта:

- один web service;
- FastAPI API + встроенный статический UI;
- embedded background worker в том же процессе;
- persistent Railway volume;
- SQLite + on-disk retrieval index.

## Quick Check

Проверка должна занимать 1-2 минуты:

1. Открыть live demo URL.
1. Нажать `Run Demo Dataset`.
1. Дождаться статуса `COMPLETED`.
1. Убедиться, что в UI отобразились:
   - clusters;
   - Markdown report;
   - timeline событий.
1. В блоке Q&A задать вопрос:
   - `What is the highest-priority issue and what evidence supports it?`

## Expected Result

На demo dataset ожидается:

- `36` обработанных отзывов;
- `0` quarantined;
- `8` кластеров;
- top issue: `Payment flow crashes`;
- grounded answer с evidence и cluster ids.

## Health Endpoints

Для технической проверки доступны:

- `/health/live`
- `/health/ready`
- `/metrics`

Ожидаемое состояние hosted deploy:

- `/health/live` -> `{"status":"ok"}`
- `/health/ready` -> `ready=true`
- `worker.mode=embedded`
- `storage.data_dir=/data/runtime`

## Demo Dataset

Для кнопки `Run Demo Dataset` используется встроенный файл:

- [data/demo/mobile_app_reviews.csv](../data/demo/mobile_app_reviews.csv)

Кратко о датасете:

- `36` отзывов;
- `18` из `app_store`, `18` из `google_play`;
- русский и английский языки;
- период с `2026-03-17` по `2026-04-12`.

## Suggested Submission Text

Ниже готовый текст, который можно использовать при отправке проекта:

```text
Repository: https://github.com/rizvansky/product-feedback-intelligence-agent
Live demo: https://product-feedback-intelligence-agent-production.up.railway.app

Что можно проверить:
1. Открыть live demo.
2. Нажать Run Demo Dataset.
3. Дождаться статуса COMPLETED.
4. Посмотреть clusters, Markdown report и timeline.
5. В Q&A задать вопрос: What is the highest-priority issue and what evidence supports it?

Ожидаемый результат:
- top issue: Payment flow crashes
- 36 processed reviews
- 8 clusters
- grounded answer with evidence
```
