import json
from pathlib import Path
from typing import Iterator

from sqlalchemy import inspect, text
from sqlmodel import Session, SQLModel, create_engine

from app.config import DATABASE_PATH

db_path = Path(DATABASE_PATH)
db_path.parent.mkdir(parents=True, exist_ok=True)

engine = create_engine(
    f"sqlite:///{db_path}",
    connect_args={"check_same_thread": False},
)

# (table, colonne) présente dès que ce déploiement a le schéma le plus récent
# pour cette table. Sert uniquement à détecter un schéma obsolète, voir
# _reset_media_cache_if_stale() — ajouter une entrée à chaque nouvelle colonne
# ajoutée sur Media/MediaFile/Torrent/ScanRun.
_CURRENT_SCHEMA_MARKERS = [
    ("torrent", "ratio"),
    ("scanrun", "qbittorrent_torrent_count"),
    ("torrent", "matched_by_name"),
    ("torrent", "repairable"),
    ("scanrun", "trigger"),
    ("media", "missing_emby_episodes"),
    ("media", "poster_image_tag"),
    ("torrent", "category"),
    ("mediafile", "arr_file_id"),
    ("media", "watch_in_progress_count"),
    ("mediawatch", "in_progress"),
    ("embyuser", "image_tag"),
    ("mediarequest", "auto_approved"),
    ("media", "arr_instance_id"),
    ("importissue", "output_path"),
    ("media", "root_path"),
    ("scanrun", "scope"),
    ("torrentfile", "torrent_hash"),
]


def _reset_media_cache_if_stale() -> None:
    """Media/MediaFile/Torrent/ScanRun sont un pur cache reconstruit à chaque
    scan : ce projet n'a pas de migrations Alembic, et `create_all()` ne
    modifie jamais une table déjà existante. Si le schéma attendu a changé
    (nouvelle colonne sur l'une de ces tables), on les recrée plutôt que de
    gérer des ALTER TABLE colonne par colonne — sans jamais toucher
    `settings`, qui contient la vraie configuration de l'utilisateur."""
    inspector = inspect(engine)
    table_names = set(inspector.get_table_names())
    if "torrent" not in table_names:
        return  # première installation : create_all() suffira à tout créer

    is_current = True
    for table, marker_column in _CURRENT_SCHEMA_MARKERS:
        if table not in table_names:
            is_current = False
            break
        existing_columns = {col["name"] for col in inspector.get_columns(table)}
        if marker_column not in existing_columns:
            is_current = False
            break
    if is_current:
        return

    with engine.begin() as conn:
        for table in (
            "torrentfile",
            "importissue",
            "mediarequest",
            "mediawatch",
            "embyuser",
            "torrent",
            "mediafile",
            "scanrun",
            "media",
        ):
            conn.execute(text(f"DROP TABLE IF EXISTS {table}"))


# Colonnes ajoutées à `settings` après la création initiale de la table.
# Contrairement au cache média, `settings` contient la config utilisateur et
# ne doit JAMAIS être recréée/vidée — on ajoute juste les colonnes
# manquantes une par une avec ALTER TABLE.
_SETTINGS_NEW_COLUMNS = [
    ("cross_seed_library_path", "VARCHAR"),
    ("scan_schedule_enabled", "BOOLEAN NOT NULL DEFAULT 0"),
    ("scan_schedule_interval_minutes", "INTEGER"),
    ("language", "VARCHAR"),
    ("update_check_enabled", "BOOLEAN NOT NULL DEFAULT 1"),
    ("excluded_emby_user_ids", "VARCHAR NOT NULL DEFAULT '[]'"),
    ("seer_enabled", "BOOLEAN NOT NULL DEFAULT 0"),
    ("seer_url", "VARCHAR"),
    ("seer_api_key", "VARCHAR"),
    ("ui_preferences", "VARCHAR NOT NULL DEFAULT '{}'"),
    ("media_server", "VARCHAR NOT NULL DEFAULT 'emby'"),
    ("notify_discord_webhook", "VARCHAR"),
    ("notify_ntfy_url", "VARCHAR"),
    ("notify_ntfy_token", "VARCHAR"),
    ("notify_gotify_url", "VARCHAR"),
    ("notify_gotify_token", "VARCHAR"),
    ("notify_on_scan", "BOOLEAN NOT NULL DEFAULT 0"),
    ("notify_on_scan_failure", "BOOLEAN NOT NULL DEFAULT 1"),
    ("notify_on_actions", "BOOLEAN NOT NULL DEFAULT 1"),
    ("widget_api_key_hash", "VARCHAR"),
    ("torrent_client", "VARCHAR NOT NULL DEFAULT 'qbittorrent'"),
    ("update_notified_version", "VARCHAR"),
    ("automation_guard_percent", "INTEGER NOT NULL DEFAULT 20"),
    ("automations_paused_at", "DATETIME"),
    ("automations_paused_reason", "VARCHAR"),
]


# Même principe pour le compte administrateur (`user`), jamais recréé.
_USER_NEW_COLUMNS = [
    ("totp_secret", "VARCHAR"),
    ("totp_pending_secret", "VARCHAR"),
    ("totp_last_step", "INTEGER"),
    ("recovery_codes", "VARCHAR NOT NULL DEFAULT '[]'"),
]


def _ensure_columns(table: str, columns: list[tuple[str, str]]) -> None:
    inspector = inspect(engine)
    if table not in inspector.get_table_names():
        return  # première installation : create_all() créera le schéma complet
    existing_columns = {col["name"] for col in inspector.get_columns(table)}
    with engine.begin() as conn:
        for column, sql_type in columns:
            if column not in existing_columns:
                conn.execute(text(f'ALTER TABLE "{table}" ADD COLUMN {column} {sql_type}'))


def _migrate_legacy_notifications() -> None:
    """Réglages de notification d'avant les canaux multiples
    (`Settings.notify_*`) : convertis en canaux, puis effacés — un secret ne
    doit vivre qu'à un seul endroit."""
    from app.models.notification_channel import NotificationChannel
    from app.models.settings import Settings

    with Session(engine) as session:
        row = session.get(Settings, 1)
        if row is None:
            return
        legacy = [
            ("discord", "Discord", row.notify_discord_webhook, None),
            ("ntfy", "ntfy", row.notify_ntfy_url, row.notify_ntfy_token),
            ("gotify", "Gotify", row.notify_gotify_url, row.notify_gotify_token),
        ]
        if not any(url for _, _, url, _ in legacy):
            return

        events = []
        if row.notify_on_scan:
            events.append("scan_completed")
        if row.notify_on_scan_failure:
            events.append("scan_failed")
        if row.notify_on_actions:
            events += ["delete_selection", "cascade_delete", "hardlink_repair", "cross_seed_search"]
        for kind, name, url, token in legacy:
            if url:
                session.add(
                    NotificationChannel(kind=kind, name=name, url=url, token=token, events=json.dumps(sorted(events)))
                )
        row.notify_discord_webhook = None
        row.notify_ntfy_url = row.notify_ntfy_token = None
        row.notify_gotify_url = row.notify_gotify_token = None
        session.add(row)
        session.commit()


def init_db() -> None:
    from app.models.activity import ActionLog  # noqa: F401
    from app.models.arr_instance import ArrInstance  # noqa: F401
    from app.models.automation import Automation  # noqa: F401
    from app.models.notification_channel import NotificationChannel  # noqa: F401
    from app.models.auth import Session as AuthSession  # noqa: F401
    from app.models.auth import User  # noqa: F401
    from app.models.media import (  # noqa: F401
        EmbyUser,
        ImportIssue,
        TorrentFile,
        Media,
        MediaFile,
        MediaRequest,
        MediaWatch,
        ScanRun,
        Torrent,
    )
    from app.models.settings import Settings  # noqa: F401

    _reset_media_cache_if_stale()
    _ensure_columns("settings", _SETTINGS_NEW_COLUMNS)
    _ensure_columns("user", _USER_NEW_COLUMNS)
    SQLModel.metadata.create_all(engine)
    _migrate_legacy_notifications()


def get_session() -> Iterator[Session]:
    with Session(engine) as session:
        yield session
