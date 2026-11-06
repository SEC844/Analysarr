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
    # Marquée : l'interface recharge la fiche au lieu de rester sur l'erreur.
    assert gone.json()["stale_selection"] is True and other.json()["stale_selection"] is True
    assert episode.exists()


# 6. Le scan complet remplace le cache en une seule transaction : une écriture
#    qui échoue laisse l'ancien cache intact au lieu d'une bibliothèque vide.
def test_a_failed_cache_replacement_keeps_the_previous_library(session, settings):
    from sqlalchemy.exc import IntegrityError
    from sqlmodel import select

    from app.services.scan.orchestrator import _replace_cache
    from app.services.scan.results import MediaBuildResult

    session.add(Media(media_type=MediaType.movie, title="Ancien", radarr_id=1))
    session.commit()
    broken = MediaBuildResult(
        media=Media(media_type=MediaType.movie, title="Nouveau", radarr_id=2),
        files=[MediaFile(path=None, size=1)],  # chemin obligatoire : l'écriture échoue
    )

    try:
        _replace_cache(session, [broken], [])
    except IntegrityError:
        session.rollback()

    assert [m.title for m in session.exec(select(Media)).all()] == ["Ancien"]


# 7. Annexes : le nom d'une AUTRE vidéo plus longue du même dossier n'est pas
#    pris pour une annexe de celle-ci.
def test_companions_of_a_single_episode_leave_the_double_episode_subtitles(tmp_path):
    from app.services.companion_files import companions

    single = tmp_path / "Show - S01E01.mkv"
    single.write_bytes(b"x")
    (tmp_path / "Show - S01E01-E02.mkv").write_bytes(b"x")
    (tmp_path / "Show - S01E01-E02.fr.srt").write_bytes(b"x")
    (tmp_path / "Show - S01E01.fr.srt").write_bytes(b"x")

    assert companions(str(single)) == [str(tmp_path / "Show - S01E01.fr.srt")]
