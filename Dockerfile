# syntax=docker/dockerfile:1

# ---- Stage 1: build the frontend static assets -----------------------------
FROM node:22-alpine AS frontend-build
WORKDIR /frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

# ---- Stage 2: Python runtime serving the API + the static frontend --------
FROM python:3.12-slim AS runtime
WORKDIR /app

COPY backend/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

COPY backend/app ./app
COPY --from=frontend-build /frontend/dist ./static

# Ces deux variables sont les seules qui restent de la configuration Docker :
# tout le reste (URLs, clés API, chemins...) se règle depuis l'assistant de
# configuration au premier lancement, en base SQLite.
ENV PORT=8000 \
    DATABASE_PATH=/data/analysarr.db \
    PYTHONUNBUFFERED=1

VOLUME ["/data"]
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import os,urllib.request,sys; urllib.request.urlopen(f'http://127.0.0.1:{os.environ[\"PORT\"]}/api/settings', timeout=3)" || exit 1

CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT}"]
