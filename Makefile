.PHONY: install install-spacy-models test run-api run-worker run-embedded demo

install:
	./.venv/bin/python -m pip install -e ".[dev]"

install-spacy-models:
	./.venv/bin/python -m spacy download en_core_web_sm
	./.venv/bin/python -m spacy download ru_core_news_sm

test:
	./.venv/bin/python -m pytest -q

run-api:
	PFIA_DATA_DIR=data/runtime PYTHONPATH=src ./.venv/bin/python -m pfia.api

run-worker:
	PFIA_DATA_DIR=data/runtime PYTHONPATH=src ./.venv/bin/python -m pfia.worker

run-embedded:
	PFIA_DATA_DIR=data/runtime PFIA_EMBEDDED_WORKER=true PYTHONPATH=src ./.venv/bin/python -m pfia.api

demo:
	docker compose up --build
