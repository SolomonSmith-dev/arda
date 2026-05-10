FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

RUN apt-get update \
 && apt-get install -y --no-install-recommends \
        curl build-essential procps \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY pyproject.toml ./
COPY core ./core
COPY agents ./agents
COPY api ./api
COPY mcp_server ./mcp_server
COPY docs ./docs

RUN pip install --upgrade pip \
 && pip install -e .
# `[full]` extra adds sentence-transformers + torch (~1GB) for real
# semantic embeddings. Skipped here so the default image stays slim;
# Finrod uses MockEmbedder when USE_MOCK_EMBEDDER is unset.

EXPOSE 5000

CMD ["uvicorn", "api.main:app", "--host", "0.0.0.0", "--port", "5000"]
