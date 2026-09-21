"""Garde-fou des montages : une suppression doit être refusée quand un volume
n'est pas monté, sinon Analysarr efface de sa base (et de Sonarr/Radarr) des
fichiers parfaitement intacts."""

import asyncio
import os

import pytest
from sqlmodel import select

from app.models.media import Media, MediaFile, MediaType
from app.schemas.media import MediaDeleteSelection
from app.services.cascade_delete import execute_delete
from app.services.media_delete import execute_media_delete
from app.services.path_guard import MountUnavailableError, ensure_paths_available, looks_unmounted


def test_an_empty_mount_point_is_recognised_but_not_a_deleted_file(tmp_path):
    mount = tmp_path / "media"  # point de montage sans volume derrière
    mount.mkdir()
    assert looks_unmounted(str(mount / "Films" / "Film.mkv"))

    (mount / "autre.mkv").write_bytes(b"x")  # le dossier vit : fichier simplement supprimé
    assert not looks_unmounted(str(mount / "Films" / "Film.mkv"))

    existing = mount / "autre.mkv"
    assert not looks_unmounted(str(existing))


def test_an_unmounted_library_root_blocks_any_action(settings, tmp_path):
    empty_root = tmp_path / "vide"
    empty_root.mkdir()
    settings.emby_library_path = str(empty_root)

    with pytest.raises(MountUnavailableError) as raised:
        ensure_paths_available(settings, [], "Suppression")
    assert str(empty_root) in str(raised.value)


def test_selective_deletion_is_refused_and_keeps_everything(session, settings, tmp_path):
    library = tmp_path / "library"
    library.mkdir()
    media = Media(media_type=MediaType.movie, title="Titre", radarr_id=42)
    session.add(media)
    session.commit()
    kept = library / "Film.mkv"
    kept.write_bytes(b"x")
    row = MediaFile(media_id=media.id, path=str(kept), size=1)
    session.add(row)
    session.commit()

    settings.emby_library_path = str(tmp_path / "jamais-monte")  # volume absent
    selection = MediaDeleteSelection(media_file_ids=[row.id], remove_from_arr=True)
    with pytest.raises(MountUnavailableError):
        asyncio.run(execute_media_delete(session, media, settings, selection))

    assert os.path.exists(kept)
    assert session.exec(select(MediaFile).where(MediaFile.media_id == media.id)).all()
    assert session.get(Media, media.id) is not None


def test_cleanup_is_refused_when_a_duplicate_sits_on_an_empty_mount(session, settings, tmp_path):
    mount = tmp_path / "media"
    mount.mkdir()  # monté… puis vidé : plus aucun fichier visible
    media = Media(media_type=MediaType.movie, title="Titre", statuses="doublon")
    session.add(media)
    session.commit()
    session.add_all(
        [
            MediaFile(media_id=media.id, path=str(mount / "Film.mkv"), size=10, is_current=False),
            MediaFile(media_id=media.id, path=str(mount / "Film.2160p.mkv"), size=20, is_current=True),
        ]
    )
    session.commit()

    with pytest.raises(MountUnavailableError):
        asyncio.run(execute_delete(session, media, settings))
    assert len(session.exec(select(MediaFile).where(MediaFile.media_id == media.id)).all()) == 2


def test_refused_deletion_answers_409(admin_client, session, settings, tmp_path):
    media = Media(media_type=MediaType.movie, title="Titre")
    session.add(media)
    session.commit()
    row = MediaFile(media_id=media.id, path=str(tmp_path / "absent" / "Film.mkv"), size=1)
    session.add(row)
    settings.emby_library_path = str(tmp_path / "jamais-monte")
    session.add(settings)
    session.commit()

    response = admin_client.post(
        f"/api/media/{media.id}/delete-selection", json={"media_file_ids": [row.id], "torrent_ids": []}
    )
    assert response.status_code == 409
    assert "montage" in response.json()["detail"] or "monté" in response.json()["detail"]
    assert session.exec(select(MediaFile).where(MediaFile.media_id == media.id)).all()


def test_an_automation_reports_the_refusal_instead_of_crashing(session, settings, tmp_path):
    """Une automatisation ne doit jamais s'arrêter sur une exception : le refus
    se lit comme une étape en échec, tracée dans l'historique."""
    from app.models.automation import Automation
    from app.services.automations import run_rule

    media = Media(media_type=MediaType.movie, title="Titre", statuses="doublon")
    session.add(media)
    session.commit()
    session.add_all(
        [
            MediaFile(media_id=media.id, path=str(tmp_path / "absent" / "vieux.mkv"), size=10, is_current=False),
            MediaFile(media_id=media.id, path=str(tmp_path / "absent" / "neuf.mkv"), size=20, is_current=True),
        ]
    )
    settings.emby_library_path = str(tmp_path / "jamais-monte")
    session.add(settings)
    session.commit()

    automation = Automation(
        name="Nettoyage doublons",
        trigger="duplicate_detected",
        action="cleanup",
        conditions="{}",
        max_actions=5,
        dry_run=False,
    )
    session.add(automation)
    session.commit()

    result = asyncio.run(run_rule(session, settings, [], automation))
    assert result.steps and not result.steps[0].success
    assert "mont" in (result.steps[0].error or "")
