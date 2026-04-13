FROM python:3.10-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

COPY pyproject.toml README.md /app/
COPY src /app/src
COPY data/demo /app/data/demo

RUN pip install .

EXPOSE 8000

CMD ["pfia-api", "--host", "0.0.0.0", "--port", "8000"]
