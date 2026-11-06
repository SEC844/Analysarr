"""Non-régression des constats de l'audit de la 1.0.3."""

from sqlmodel import delete

from app.models.media import Media, MediaFile, MediaType, Torrent
from app.schemas.media import MediaDeleteSelection
from app.services.ignores import media_key
from app.services.media_delete import resolve_selection


# 1. Identifiants recyclés : une réanalyse efface et réinsère torrents et
#    fichiers, SQLite réattribue les identifiants. La sélection désigne donc
#    les éléments par clé stable (hash, chemin) et une clé périmée est refusée.
def test_a_selection_made_before_a_rebuild_still_targets_the_chosen_torrent(session, settings):
    media = Media(media_type=MediaType.movie, title="Film")
    session.add(media)
    session.commit()
    session.add_all(
        [
            Torrent(media_id=media.id, hash="aaa", name="Film YGG"),
            Torrent(media_id=media.id, hash="bbb", name="Film C411"),
        ]
    )
    session.commit()
    chosen = MediaDeleteSelection(torrent_hashes=["BBB"], media_key=media_key(media))  # copie C411 cochée

    # Réanalyse des torrents (partial_scan._store_torrents) : tout est effacé
    # puis réinséré, dans l'ordre que renvoie le client.
    session.exec(delete(Torrent))
    session.commit()
    session.add_all(
        [
            Torrent(media_id=media.id, hash="bbb", name="Film C411"),
            Torrent(media_id=media.id, hash="aaa", name="Film YGG"),
        ]
    )
    session.commit()

    torrents, _ = resolve_selection(session, media, chosen)
    assert [t.name for t in torrents] == ["Film C411"]


def test_a_stale_selection_is_refused_never_silently_ignored(admin_client, session, settings, tmp_path):
    media = Media(media_type=MediaType.series, title="Série")
    session.add(media)
    session.commit()
    episode = tmp_path / "e1.mkv"
    episode.write_bytes(b"x")
    session.add(MediaFile(media_id=media.id, path=str(episode), size=1))
    session.commit()
    key = media_key(media)
    url = f"/api/media/{media.id}/delete-selection"

    # Fichier renommé par Sonarr entre l'ouverture du dialogue et la confirmation.
    gone = admin_client.post(url, json={"media_file_paths": [str(tmp_path / "ancien.mkv")], "media_key": key})
    # Scan complet entre-temps : l'identifiant de l'adresse désigne un autre média.
    other = admin_client.post(url, json={"media_file_paths": [str(episode)], "media_key": "series:arr:0:999"})

    assert gone.status_code == 409 and other.status_code == 409
    assert "rechargez" in gone.json()["detail"]
    assert episode.exists()
