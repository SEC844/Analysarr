"""Cache disque des jaquettes Emby, à côté de la base SQLite (même volume
Docker persistant) — évite de retélécharger une jaquette déjà connue à
chaque affichage. Clé de cache : (emby_item_id, poster_image_tag) — un tag
différent (jaquette changée côté Emby, capturé à chaque scan) ne matche
jamais un fichier existant, donc jamais de contenu périmé servi. Les
anciennes versions d'un même item sont supprimées à chaque écriture."""

import re

from app.config import POSTER_CACHE_DIR

_UNSAFE_CHARS = re.compile(r"[^A-Za-z0-9_-]")
_CONTENT_TYPE_BY_EXT = {".jpg": "image/jpeg", ".png": "image/png", ".webp": "image/webp"}
_EXT_BY_CONTENT_TYPE = {v: k for k, v in _CONTENT_TYPE_BY_EXT.items()}
_DEFAULT_EXT = ".img"


def _sanitize(value: str) -> str:
    return _UNSAFE_CHARS.sub("_", value)


def _cache_key(emby_item_id: str, image_tag: str | None) -> str:
    return f"{_sanitize(emby_item_id)}__{_sanitize(image_tag or 'no-tag')}"


def read_cached_poster(emby_item_id: str, image_tag: str | None) -> tuple[bytes, str] | None:
    key = _cache_key(emby_item_id, image_tag)
    for path in POSTER_CACHE_DIR.glob(f"{key}.*"):
        try:
            content = path.read_bytes()
        except OSError:
            return None
        return content, _CONTENT_TYPE_BY_EXT.get(path.suffix, "application/octet-stream")
    return None


def write_cached_poster(emby_item_id: str, image_tag: str | None, content: bytes, content_type: str) -> None:
    POSTER_CACHE_DIR.mkdir(parents=True, exist_ok=True)

    # Purge les anciennes versions de CET item (tag différent ou absent) :
    # sans ça, chaque changement de jaquette laisserait un fichier orphelin
    # de plus, jamais nettoyé.
    stale_prefix = f"{_sanitize(emby_item_id)}__"
    for stale in POSTER_CACHE_DIR.glob(f"{stale_prefix}*"):
        stale.unlink(missing_ok=True)

    ext = _EXT_BY_CONTENT_TYPE.get(content_type, _DEFAULT_EXT)
    path = POSTER_CACHE_DIR / f"{_cache_key(emby_item_id, image_tag)}{ext}"
    path.write_bytes(content)
