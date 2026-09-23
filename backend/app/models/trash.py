from datetime import UTC, datetime

from sqlmodel import Field, SQLModel


def _utcnow() -> datetime:
    # Naïf (UTC), comme le reste des tables : SQLite relit sans fuseau.
    return datetime.now(UTC).replace(tzinfo=None)


class TrashAction(SQLModel, table=True):
    """Une suppression entière mise de côté : ses fichiers de bibliothèque, ses
    torrents, et de quoi remettre le média dans Sonarr/Radarr et Seer.

    La restauration se fait par ACTION, jamais élément par élément : rendre la
    moitié d'une suppression laisserait une bibliothèque incohérente (voir
    services/trash.py)."""

    id: int | None = Field(default=None, primary_key=True)
    created_at: datetime = Field(default_factory=_utcnow, index=True)

    # delete_selection | cascade_delete | automation
    action: str = "delete_selection"
    media_title: str = ""
    media_type: str | None = None

    # Fiche Sonarr/Radarr capturée AVANT la suppression, pour la recréer telle
    # quelle : {"service": "radarr"|"sonarr", "instance_id": int|null, "body": {...}}
    arr_payload: str | None = None


class TrashItem(SQLModel, table=True):
    """Élément d'une action : un fichier de bibliothèque, ou les données d'un
    torrent (retiré du client SANS supprimer ses fichiers, voir trash.py)."""

    id: int | None = Field(default=None, primary_key=True)
    action_id: int = Field(foreign_key="trashaction.id", index=True)

    # library_file | torrent
    kind: str = "library_file"
    label: str = ""
    size: int = 0

    original_path: str | None = None
    trashed_path: str | None = None

    # Torrents : {"hash", "name", "save_path", "category", "magnet", "torrent_b64"}
    torrent_payload: str | None = None
