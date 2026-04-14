FROM python:3.10-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

RUN pip install "chromadb==1.5.7"

EXPOSE 8000

CMD ["sh", "-c", "chroma run --host 0.0.0.0 --port ${PORT:-8000} --path /data/chroma"]
