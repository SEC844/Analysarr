#!/bin/sh
# Lance Analysarr sous l'utilisateur demandé plutôt qu'en root.
#
# Analysarr crée et supprime des fichiers dans la bibliothèque (réparation de
# hardlinks, corbeille). En root, ces fichiers appartiennent à root et Sonarr,
# Radarr ou le client torrent ne peuvent plus les gérer. PUID/PGID reprennent
# la convention des images LinuxServer : mettez les mêmes valeurs que sur vos
# autres conteneurs.
set -eu

PUID="${PUID:-1000}"
PGID="${PGID:-1000}"
FILE_UMASK="${UMASK:-002}"

# Conteneur déjà lancé en non-root (docker run --user) : rien à préparer, on
# ne pourrait de toute façon ni créer un utilisateur ni changer de propriétaire.
if [ "$(id -u)" -ne 0 ]; then
    umask "$FILE_UMASK"
    exec "$@"
fi

# Même résolution que backend/app/config.py : sans DATABASE_PATH, la base vit
# dans /config, sauf si une installation existante l'a laissée dans /data.
db_path="${DATABASE_PATH:-}"
if [ -z "$db_path" ]; then
    if [ ! -f /config/analysarr.db ] && [ -f /data/analysarr.db ]; then
        db_path=/data/analysarr.db
    else
        db_path=/config/analysarr.db
    fi
fi
db_dir="$(dirname "$db_path")"

# Groupe puis utilisateur : on réutilise ceux qui portent déjà ces identifiants
# (l'image en contient quelques-uns), sinon on les crée.
group_name="$(getent group "$PGID" | cut -d: -f1)"
if [ -z "$group_name" ]; then
    groupadd -g "$PGID" analysarr
    group_name=analysarr
fi
user_name="$(getent passwd "$PUID" | cut -d: -f1)"
if [ -z "$user_name" ]; then
    useradd -u "$PUID" -g "$PGID" -M -s /usr/sbin/nologin analysarr
    user_name=analysarr
fi

mkdir -p "$db_dir" "$db_dir/posters"

# Propriétaire : UNIQUEMENT ce qui appartient à Analysarr (sa base et son cache
# de jaquettes). Jamais /data ni les médias — un chown récursif sur une
# bibliothèque entière casserait les droits de toute la stack.
case "$db_dir" in
    /|/data|/data/*) chown "$PUID:$PGID" "$db_dir" 2>/dev/null || true ;;
    *) chown -R "$PUID:$PGID" "$db_dir" 2>/dev/null || true ;;
esac
chown -R "$PUID:$PGID" "$db_dir/posters" 2>/dev/null || true
for suffix in "" -wal -shm; do
    [ -e "$db_path$suffix" ] && chown "$PUID:$PGID" "$db_path$suffix" 2>/dev/null || true
done

umask "$FILE_UMASK"

# setpriv vient d'util-linux, présent dans python:3.12-slim ; gosu sert de
# repli si une image future ne l'embarque plus.
if command -v setpriv >/dev/null 2>&1; then
    exec setpriv --reuid="$PUID" --regid="$PGID" --init-groups "$@"
fi
if command -v gosu >/dev/null 2>&1; then
    exec gosu "$PUID:$PGID" "$@"
fi
echo "analysarr: ni setpriv ni gosu disponibles, démarrage en root" >&2
exec "$@"
