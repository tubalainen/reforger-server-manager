# ---- Stage 1: build the Vue SPA --------------------------------------------
FROM node:24-alpine AS frontend
WORKDIR /build
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

# ---- Stage 2: FastAPI runtime ------------------------------------------------
FROM python:3.12-slim

# Non-root user; docker-socket access is granted at runtime via compose group_add
RUN useradd --uid 1000 --create-home app \
    && mkdir -p /data && chown app:app /data

WORKDIR /app
COPY backend/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY backend/ /app/
COPY --from=frontend /build/dist /app/static

ENV STATIC_DIR=/app/static \
    DATA_DIR=/data \
    PYTHONUNBUFFERED=1

USER app
EXPOSE 8080
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8080"]
