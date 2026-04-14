FROM python:3.10-slim

ARG PFIA_INSTALL_SPACY_MODELS=true
ARG PFIA_INSTALL_LOCAL_EMBEDDINGS=false

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PFIA_INSTALL_SPACY_MODELS=${PFIA_INSTALL_SPACY_MODELS} \
    PFIA_INSTALL_LOCAL_EMBEDDINGS=${PFIA_INSTALL_LOCAL_EMBEDDINGS}

WORKDIR /app

COPY pyproject.toml README.md /app/
COPY src /app/src
COPY data/demo /app/data/demo

RUN if [ "$PFIA_INSTALL_LOCAL_EMBEDDINGS" = "true" ]; then \
      pip install ".[local-embeddings]"; \
    else \
      pip install .; \
    fi && \
    if [ "$PFIA_INSTALL_SPACY_MODELS" = "true" ]; then \
      python -m spacy download en_core_web_sm && \
      python -m spacy download ru_core_news_sm; \
    fi

EXPOSE 8000

CMD ["pfia-api", "--host", "0.0.0.0", "--port", "8000"]
