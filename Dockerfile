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

# Which optional extras to bake in. Default is slim -- Finrod falls back to
# MockEmbedding and the image stays ~400MB. Build with
#   docker compose build --build-arg ARDA_EXTRAS=embeddings
# (or set ARDA_EXTRAS in .env, which compose passes through) to add
# sentence-transformers + torch (~1GB) for real semantic embeddings.
# `full` additionally pulls pymilvus, which needs a Milvus server to be
# useful and an SSE4.2-capable CPU to run against.
ARG ARDA_EXTRAS=""
RUN pip install --upgrade pip \
 && if [ -n "$ARDA_EXTRAS" ]; then \
        pip install -e ".[$ARDA_EXTRAS]"; \
    else \
        pip install -e .; \
    fi

EXPOSE 5000

CMD ["uvicorn", "api.main:app", "--host", "0.0.0.0", "--port", "5000"]
