# ---- Stage 1: build the Vue SPA --------------------------------------------
FROM node:24-alpine AS frontend
WORKDIR /build
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

# ---- Test stage (skipped in normal builds): docker build --target test . -----
FROM python:3.12-slim AS test
WORKDIR /app
COPY backend/requirements.txt backend/requirements-dev.txt ./
RUN pip install --no-cache-dir -r requirements-dev.txt
COPY backend/ /app/
RUN python -m pytest tests -q

# ---- Stage 2: FastAPI runtime ------------------------------------------------
FROM python:3.12-slim

# Unprivileged runtime user; the entrypoint starts as root for first-run
# setup (chown /data, docker-socket group) and drops to 'app' via gosu.
RUN useradd --uid 1000 --create-home app \
    && mkdir -p /data && chown app:app /data \
    && apt-get update \
    && apt-get install -y --no-install-recommends gosu \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY backend/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY backend/ /app/
COPY --from=frontend /build/dist /app/static

ENV STATIC_DIR=/app/static \
    DATA_DIR=/data \
    PYTHONUNBUFFERED=1

RUN chmod +x /app/entrypoint.sh

EXPOSE 8080
ENTRYPOINT ["/app/entrypoint.sh"]
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8080"]
