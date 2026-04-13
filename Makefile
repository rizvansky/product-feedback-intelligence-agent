.PHONY: install test run-api run-worker run-embedded demo

install:
	./.venv/bin/python -m pip install -e ".[dev]"

test:
	./.venv/bin/python -m pytest -q

run-api:
	PYTHONPATH=src ./.venv/bin/python -m pfia.api

run-worker:
	PYTHONPATH=src ./.venv/bin/python -m pfia.worker

run-embedded:
	PFIA_EMBEDDED_WORKER=true PYTHONPATH=src ./.venv/bin/python -m pfia.api

demo:
	docker compose up --build
