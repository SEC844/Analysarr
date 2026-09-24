# syntax=docker/dockerfile:1

# ---- Stage 1: build the frontend static assets -----------------------------
# Toujours exécuté sur l'architecture native du builder ($BUILDPLATFORM) : le
# résultat (HTML/JS/CSS statiques) est identique pour toutes les plateformes,
# et `npm ci` sous émulation QEMU ARM64 restait régulièrement bloqué des heures
# sur les runners GitHub. Construit une seule fois, copié dans chaque image.
FROM --platform=$BUILDPLATFORM node:22-alpine AS frontend-build
WORKDIR /frontend
# Version connue dès le build du frontend : elle sert uniquement à teinter le
# point du logo en rouge sur une image `dev`, pour distinguer d'un coup d'œil
# une instance de développement d'une instance de production (y compris sur
# l'écran de connexion, où l'API n'est pas encore interrogeable).
ARG APP_VERSION=dev
ENV VITE_APP_VERSION=${APP_VERSION}
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

COPY docker/entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod 755 /usr/local/bin/entrypoint.sh

# Seules variables de la configuration Docker : tout le reste (URLs, clés API,
# chemins...) se règle depuis l'assistant au premier lancement, en base SQLite.
# PUID/PGID/UMASK (facultatifs, voir docker/entrypoint.sh) : identité sous
# laquelle tourne Analysarr, les mêmes que Sonarr, Radarr et le client torrent.
# Volontairement sans valeur par défaut : sans eux, Analysarr reste en root
# comme les versions précédentes, pour ne casser aucune installation.
# DATABASE_PATH n'est volontairement PAS fixé non plus : sans lui, la base
# vit dans /config/analysarr.db, sauf pour une installation existante qui
# l'avait dans /data (backend/app/config.py). Un ENV la rendrait toujours
# « définie » et rendrait cette reprise impossible.
ENV PORT=1818 \
    PYTHONUNBUFFERED=1

# Identité du build, affichée dans Réglages → Application et utilisée pour
# détecter une mise à jour. Renseignée par la CI (build-args) ; placée après
# les couches coûteuses pour ne jamais invalider leur cache.
ARG APP_VERSION=dev
ARG APP_REVISION=
ARG APP_BUILD_DATE=
ENV APP_VERSION=${APP_VERSION} \
    APP_REVISION=${APP_REVISION} \
    APP_BUILD_DATE=${APP_BUILD_DATE}

VOLUME ["/config"]
EXPOSE 1818

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import os,urllib.request,sys; urllib.request.urlopen(f'http://127.0.0.1:{os.environ[\"PORT\"]}/api/health', timeout=3)" || exit 1

ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT}"]
