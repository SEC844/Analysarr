"""Fichiers annexes (NFO, sous-titres, images) et nettoyage des dossiers.

Bugs réels : une suppression laissait les NFO derrière (et un dossier vide),
et une restauration rendait la vidéo sans eux — ils n'avaient jamais été mis
de côté."""

import asyncio
import os

from sqlmodel import select

from app.models.media import Media, MediaFile, MediaType
from app.models.trash import TrashAction, TrashItem
from app.schemas.media import MediaDeleteSelection
from app.services.companion_files import companions, has_video, is_inside, prune_empty_dirs
from app.services.media_delete import execute_media_delete
from app.services.trash import restore_action
from tests.test_trash import FakeTorrentClient, enable_trash, patch_client


def movie_folder(tmp_path, session, *, with_companions=True):
    """Un film rangé comme dans une vraie bibliothèque : son dossier, sa vidéo
    et ses annexes."""
    folder = tmp_path / "Film (2024)"
    folder.mkdir()
    video = folder / "Film (2024) - Bluray-1080p.mkv"
    video.write_bytes(b"video")
    extras = []
    if with_companions:
        for name in (
            "Film (2024) - Bluray-1080p.nfo",
            "Film (2024) - Bluray-1080p.fr.srt",
            "Film (2024) - Bluray-1080p-fanart.jpg",
        ):
            path = folder / name
            path.write_bytes(b"meta")
            extras.append(path)
        poster = folder / "poster.jpg"  # annexe du DOSSIER, pas du fichier
        poster.write_bytes(b"image")
        extras.append(poster)

    media = Media(media_type=MediaType.movie, title="Film")
    session.add(media)
    session.commit()
    row = MediaFile(media_id=media.id, path=str(video), size=5)
    session.add(row)
    session.commit()
    return media, row, folder, extras


def test_companions_follow_the_video_name():
    assert companions(__file__) == []  # aucun fichier annexe à côté d'un .py


def test_companions_are_found_by_their_stem(tmp_path, session):
    _media, row, _folder, _extras = movie_folder(tmp_path, session)

    found = {os.path.basename(path) for path in companions(row.path)}

    assert found == {
        "Film (2024) - Bluray-1080p.nfo",
        "Film (2024) - Bluray-1080p.fr.srt",
        "Film (2024) - Bluray-1080p-fanart.jpg",
    }
    # `poster.jpg` appartient au dossier, pas au fichier : il ne part qu'avec lui.
    assert all("poster.jpg" not in path for path in companions(row.path))


def test_deleting_a_movie_takes_its_companions_and_its_folder(session, settings, tmp_path, monkeypatch):
    patch_client(monkeypatch, FakeTorrentClient())
    settings.emby_library_path = str(tmp_path)
    session.add(settings)
    session.commit()
    media, row, folder, _extras = movie_folder(tmp_path, session)

    asyncio.run(execute_media_delete(session, media, settings, MediaDeleteSelection(media_file_ids=[row.id])))

    # Plus de vidéo, plus de NFO, plus de dossier : rien ne traîne.
    assert not folder.exists()


def test_the_library_root_is_never_removed(session, settings, tmp_path, monkeypatch):
    """Un film posé à plat dans la racine : ses annexes partent, la racine reste."""
    patch_client(monkeypatch, FakeTorrentClient())
    settings.emby_library_path = str(tmp_path)
    session.add(settings)
    session.commit()
    video = tmp_path / "Film.mkv"
    video.write_bytes(b"video")
    (tmp_path / "Film.nfo").write_bytes(b"meta")
    media = Media(media_type=MediaType.movie, title="Film")
    session.add(media)
    session.commit()
    row = MediaFile(media_id=media.id, path=str(video), size=5)
    session.add(row)
    session.commit()

    asyncio.run(execute_media_delete(session, media, settings, MediaDeleteSelection(media_file_ids=[row.id])))

    assert tmp_path.exists() and not (tmp_path / "Film.nfo").exists()


def test_a_folder_keeping_a_video_is_left_alone(session, settings, tmp_path, monkeypatch):
    """Suppression d'un seul épisode : le dossier de la série garde ses autres
    fichiers, donc rien n'est nettoyé."""
    patch_client(monkeypatch, FakeTorrentClient())
    settings.emby_library_path = str(tmp_path)
    session.add(settings)
    session.commit()
    folder = tmp_path / "Série"
    folder.mkdir()
    first, second = folder / "S01E01.mkv", folder / "S01E02.mkv"
    for path in (first, second):
        path.write_bytes(b"video")
    (folder / "tvshow.nfo").write_bytes(b"meta")
    media = Media(media_type=MediaType.series, title="Série")
    session.add(media)
    session.commit()
    rows = [MediaFile(media_id=media.id, path=str(path), size=5, episode_label=f"S01E0{i + 1}") for i, path in enumerate((first, second))]
    session.add_all(rows)
    session.commit()

    asyncio.run(execute_media_delete(session, media, settings, MediaDeleteSelection(media_file_ids=[rows[0].id])))

    assert not first.exists() and second.exists()
    assert (folder / "tvshow.nfo").exists() and has_video(str(folder))


def test_the_trash_keeps_the_companions_and_gives_them_back(session, settings, tmp_path, monkeypatch):
    enable_trash(session, settings, tmp_path)
    patch_client(monkeypatch, FakeTorrentClient())
    media, row, folder, extras = movie_folder(tmp_path, session)
    originals = [row.path, *(str(path) for path in extras)]

    asyncio.run(execute_media_delete(session, media, settings, MediaDeleteSelection(media_file_ids=[row.id])))

    assert not folder.exists()
    action = session.exec(select(TrashAction)).one()
    items = session.exec(select(TrashItem).where(TrashItem.action_id == action.id)).all()
    # Vidéo + 3 annexes du fichier + l'affiche du dossier.
    assert {os.path.basename(item.original_path) for item in items} == {
        os.path.basename(path) for path in originals
    }

    steps, complete = asyncio.run(restore_action(session, settings, action))

    assert complete, [step.error for step in steps]
    assert all(os.path.exists(path) for path in originals)


def test_prune_stops_at_the_root(tmp_path):
    root = tmp_path / "media"
    deep = root / "Série" / "Season 01"
    deep.mkdir(parents=True)

    removed = prune_empty_dirs(str(deep), str(root))

    assert removed == [str(deep), str(root / "Série")]
    assert root.exists()
    assert is_inside(str(deep), str(root)) and not is_inside(str(root), str(root))
