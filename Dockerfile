# ---------- stage 1: build the single-page frontend ----------
FROM node:22-alpine AS frontend

WORKDIR /build
# Copy manifests first so the dependency layer is cached across source edits.
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci

COPY frontend/tsconfig.json frontend/vite.config.ts frontend/index.html ./
COPY frontend/src ./src
RUN npm run build


# ---------- stage 2: runtime ----------
FROM python:3.12-slim AS runtime

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PTB_DATA_DIR=/data \
    PTB_STATIC_DIR=/app/static

WORKDIR /app

# Pinned so an image rebuild cannot silently change behaviour.
RUN pip install --no-cache-dir \
      "fastapi==0.141.1" \
      "uvicorn[standard]==0.40.0" \
      "httpx==0.28.1" \
      "pydantic==2.13.4"

COPY backend/app ./app
COPY --from=frontend /build/dist ./static

# A fresh named volume mounted here inherits this ownership, so the app can write
# its save without a start-up chown. A bind mount keeps the host's ownership
# instead — see the README for the uid note.
RUN mkdir -p /data && chown 1000:1000 /data

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/api/health', timeout=4).status == 200 else 1)"

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--log-level", "info"]
