# ---- Stage 1: build the Vue SPA --------------------------------------------
FROM node:24-alpine AS frontend
WORKDIR /build
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

# ---- Test stage (skipped in normal builds): docker build --target test . -----
FROM python:3.13-slim AS test
WORKDIR /app
COPY backend/requirements.txt backend/requirements-dev.txt ./
RUN pip install --no-cache-dir -r requirements-dev.txt
COPY backend/ /app/
RUN python -m pytest tests -q

# ---- Stage 2: FastAPI runtime ------------------------------------------------
# Python 3.13: several CPython CVEs are fixed only from 3.13 on, never in 3.12
# (grype on the v0.97.0 image). Not 3.14 yet: python:3.14-slim had 3.14.10,
# with a High CVE (CVE-2026-119170) fixed only in 3.14.11.
FROM python:3.13-slim

# Unprivileged runtime user; the entrypoint starts as root for first-run
# setup (chown /data, docker-socket group) and drops to 'app' with setpriv
# (util-linux, part of Debian itself, so apt-get upgrade keeps it patched).
# It replaced gosu, whose Debian build carried an old Go runtime with known CVEs.
# apt-get upgrade: Debian's security fixes reach the image the day it is built,
# not whenever python:3.13-slim is next rebuilt (zlib1g CVE-2026-76844 and
# CVE-2026-85091 were fixed in Debian while the base image still had the old one).
RUN useradd --uid 1000 --create-home app \
    && mkdir -p /data && chown app:app /data \
    && apt-get update \
    && apt-get upgrade -y --no-install-recommends \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY backend/requirements.txt .
# The base image's pip lags behind its security fixes: upgrade it first.
RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir -r requirements.txt

COPY backend/ /app/
COPY --from=frontend /build/dist /app/static

# The version this image is, for the Server Supervisor, which reads it from each
# stack's manager container (#204). CI keeps it equal to backend/config.py.
LABEL reforger-manager.version="0.97.2"

ENV STATIC_DIR=/app/static \
    DATA_DIR=/data \
    PYTHONUNBUFFERED=1

RUN chmod +x /app/entrypoint.sh

EXPOSE 8080
ENTRYPOINT ["/app/entrypoint.sh"]
# --timeout-graceful-shutdown: without it uvicorn waits FOREVER for open
# connections (an open GUI tab's log-stream WebSocket) before running the
# lifespan shutdown that stops the game servers (#113) — compose would
# SIGKILL us before the cleanup ever started.
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8080", "--timeout-graceful-shutdown", "5"]
