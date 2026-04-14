.PHONY: install install-spacy-models frontend-install test evals run-api run-worker run-embedded run-frontend demo

install:
	./.venv/bin/python -m pip install -e ".[dev]"

install-spacy-models:
	./.venv/bin/python -m spacy download en_core_web_sm
	./.venv/bin/python -m spacy download ru_core_news_sm

frontend-install:
	cd frontend && npm ci

test:
	./.venv/bin/python -m pytest -q

evals:
	PFIA_DATA_DIR=data/runtime PYTHONPATH=src ./.venv/bin/python -m pfia.evals

run-api:
	PFIA_DATA_DIR=data/runtime PYTHONPATH=src ./.venv/bin/python -m pfia.api

run-worker:
	PFIA_DATA_DIR=data/runtime PYTHONPATH=src ./.venv/bin/python -m pfia.worker

run-embedded:
	PFIA_DATA_DIR=data/runtime PFIA_EMBEDDED_WORKER=true PYTHONPATH=src ./.venv/bin/python -m pfia.api

run-frontend:
	cd frontend && PFIA_INTERNAL_API_BASE_URL=http://127.0.0.1:8000 npm run dev

demo:
	docker compose up --build
