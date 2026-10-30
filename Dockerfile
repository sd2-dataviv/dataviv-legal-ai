# Dataviv AI OS - one image for the API (with the built test console at /ui) and the background worker.
#
#   docker compose up -d --build          # whole stack: postgres, qdrant, api (see docker-compose.yml)
#   docker build -t dataviv-ai-os .       # image only
#   docker build --build-arg SEMANTIC=0 . # slim image without PyTorch (keyword search only, or TEI over http)
#
# Stage 1 builds the React console; stage 2 installs Python dependencies into /opt/venv; stage 3 is the
# runtime, which runs as an unprivileged user. Layers are ordered so code edits rebuild in seconds.

ARG PYTHON_IMAGE=python:3.12.8-slim-bookworm

# ------------------------------------------------------------------ 1. console (React + Vite)
FROM node:20.18.1-bookworm-slim AS console
WORKDIR /src/frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund --progress=false
COPY frontend/ ./
RUN npm run build

# ------------------------------------------------------------------ 2. Python dependencies
FROM ${PYTHON_IMAGE} AS deps
COPY --from=ghcr.io/astral-sh/uv:0.5.11 /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never
# SEMANTIC=1 adds CPU PyTorch + sentence-transformers for in-process bge-m3 embeddings (~1 GB).
ARG SEMANTIC=1
RUN uv venv /opt/venv
ENV VIRTUAL_ENV=/opt/venv PATH=/opt/venv/bin:$PATH
COPY backend/pyproject.toml /tmp/pyproject.toml
# (no BuildKit cache mount, so the file also builds on hosts using the classic builder)
RUN if [ "$SEMANTIC" = "1" ]; then \
      echo "[deps] installing CPU PyTorch for local embeddings"; \
      uv pip install --index-url https://download.pytorch.org/whl/cpu torch && \
      echo "[deps] installing API dependencies + sentence-transformers" && \
      uv pip install -r /tmp/pyproject.toml --extra local-embeddings; \
    else \
      echo "[deps] installing API dependencies (no local embeddings)"; \
      uv pip install -r /tmp/pyproject.toml; \
    fi

# ------------------------------------------------------------------ 3. runtime
FROM ${PYTHON_IMAGE} AS runtime
LABEL org.opencontainers.image.title="Dataviv AI OS" \
      org.opencontainers.image.description="Legal AI operating system - API, worker and test console" \
      org.opencontainers.image.vendor="Dataviv Technologies Pvt. Ltd."

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    VIRTUAL_ENV=/opt/venv \
    PATH=/opt/venv/bin:$PATH \
    HF_HOME=/app/var/hf \
    DATAVIV_PORT=8010

# tini reaps zombies and forwards signals so uvicorn and the worker shut down cleanly.
RUN apt-get update \
 && apt-get install -y --no-install-recommends tini \
 && rm -rf /var/lib/apt/lists/* \
 && groupadd --system --gid 10001 dataviv \
 && useradd --system --uid 10001 --gid dataviv --home-dir /app --shell /usr/sbin/nologin dataviv

WORKDIR /app
COPY --from=deps /opt/venv /opt/venv
# Same layout as the repository, so the paths the code derives (frontend/dist, var/storage) still hold.
COPY backend/ backend/
COPY scripts/seed_dev.py scripts/reindex_vectors.py scripts/check_llm.py docker/starting.py scripts/
COPY --from=console /src/frontend/dist frontend/dist
COPY docker/entrypoint.sh /usr/local/bin/dataviv
RUN chmod 0755 /usr/local/bin/dataviv \
 && mkdir -p var/storage var/hf \
 && chown -R dataviv:dataviv var

USER dataviv
VOLUME ["/app/var"]
EXPOSE 8010

HEALTHCHECK --interval=15s --timeout=5s --start-period=60s --retries=5 \
  CMD python -c "import os,urllib.request; urllib.request.urlopen(f'http://127.0.0.1:{os.environ.get(\"DATAVIV_PORT\",\"8010\")}/health', timeout=4)" || exit 1

ENTRYPOINT ["/usr/bin/tini", "--", "dataviv"]
CMD ["api"]
