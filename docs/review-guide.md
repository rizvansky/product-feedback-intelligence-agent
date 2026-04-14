# Async Review Guide

Этот файл нужен для асинхронной проверки проекта ментором или преподавателем.

## Links

- Repository: `https://github.com/rizvansky/product-feedback-intelligence-agent`
- Main UI: `https://frontend-production-c4b0.up.railway.app`
- API: `https://api-production-242f.up.railway.app`
- Chroma heartbeat: `https://chroma-production-4408.up.railway.app/api/v2/heartbeat`

Публичный деплой проверен 14 апреля 2026 года.

## What Is Deployed

В репозитории подготовлен canonical Railway-профиль проекта:

- `frontend` service на `Next.js`;
- `api` service с FastAPI API и embedded worker;
- `chroma` service как отдельный HTTP vector store;
- persistent Railway volumes для `api` и `chroma`.

Упрощённый single-service профиль тоже доступен в коде, но он считается operational fallback, а не основным proposal-aligned deploy.

## Quick Check

Проверка должна занимать 1-2 минуты:

1. Открыть live demo URL.
   Main UI URL: `https://frontend-production-c4b0.up.railway.app`
1. Нажать `Run Demo Dataset`.
1. Дождаться статуса `COMPLETED`.
1. Убедиться, что в UI отобразились:
   - clusters;
   - weak signals, если они появились;
   - simple list view для low-data batch'ей;
   - Markdown report;
   - timeline событий.
   - runtime metadata.
1. В блоке Q&A задать вопрос:
   - `What is the highest-priority issue and what evidence supports it?`

## Expected Result

На demo dataset ожидается:

- `36` обработанных отзывов;
- `0` quarantined;
- `8` кластеров;
- top issue: `Payment flow crashes`;
- grounded answer с evidence и cluster ids.

Дополнительно после завершения batch-run в UI и `GET /api/sessions/{session_id}` видны:

- `runtime_profile`
- `presentation_mode`
- effective orchestrator backend
- effective generation backend
- effective retrieval backend
- input filename
- weak signal ids
- mixed sentiment ids
- per-agent usage snapshot

## Health Endpoints

Для технической проверки доступны:

- `https://api-production-242f.up.railway.app/health/live`
- `https://api-production-242f.up.railway.app/health/ready`
- `https://api-production-242f.up.railway.app/metrics`
- `https://chroma-production-4408.up.railway.app/api/v2/heartbeat`

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

## Arbitrary Run

Чтобы показать, что проект не hardcoded под встроенный demo dataset, можно сделать второй прогон:

1. Подготовить свой CSV или JSON с полями `review_id`, `source`, `text`, `created_at`.
1. Загрузить его через UI или `POST /api/sessions/upload`.
1. Дождаться завершения batch-job.
1. Проверить, что:
   - `runtime_metadata.input_filename` совпадает с именем загруженного файла;
   - `records_total` и `records_kept` отличаются от demo batch, если файл другой;
   - `top_cluster_ids`, report и Q&A меняются вместе с содержимым входного файла.

Для CLI smoke-run можно использовать:

```bash
python check.py --file path/to/your_reviews.csv --question "Which topic is spiking this week?"
```

## Suggested Submission Text

Ниже готовый текст, который можно использовать при отправке проекта:

```text
Repository: https://github.com/rizvansky/product-feedback-intelligence-agent
Main UI: https://frontend-production-c4b0.up.railway.app
API: https://api-production-242f.up.railway.app

Что можно проверить:
1. Открыть main UI.
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
