"""Éléments ignorés (services/ignores.py) : un torrent, un fichier gardé
volontairement ou une alerte masquée — tant que la situation ignorée reste la
même. Dès qu'elle change ou se résout, la règle est retirée et l'alerte
revient."""

import asyncio

from sqlmodel import select

from app.models.activity import ActionLog
from app.models.ignore import IgnoreRule
from app.models.media import Media, MediaFile, MediaType, Torrent
from app.services.automations import _concerned_torrents
from app.services.cascade_delete import build_delete_preview
from app.services.hardlink_repair import build_repair_preview
from app.services.ignores import IgnoreSet, media_key
from app.services.media_status import apply_statuses, refresh_media_statuses


def movie(session, **fields):
    media = Media(media_type=MediaType.movie, title="Matrix", radarr_id=1, emby_item_id="e1", **fields)
    session.add(media)
    session.commit()
    return media


def add_torrent(session, media, torrent_hash="AAAA", **fields):
    torrent = Torrent(media_id=media.id, hash=torrent_hash, name=f"Matrix.{torrent_hash}", size=10, **fields)
    session.add(torrent)
    session.commit()
    return torrent


def add_file(session, media, path, inode, *, is_current=False, size=10):
    row = MediaFile(media_id=media.id, path=path, size=size, inode=inode, device=1, is_current=is_current)
    session.add(row)
    session.commit()
    return row


def statuses(session, media):
    # L'API écrit dans sa propre session : relire ce qu'elle a enregistré.
    session.expire_all()
    return set(filter(None, media.statuses.split(","))), set(filter(None, media.muted_statuses.split(",")))


def actions(session, action):
    return [entry for entry in session.exec(select(ActionLog)).all() if entry.action == action]


def ignore(admin_client, media, **payload):
    return admin_client.post("/api/ignores", json={"media_id": media.id, **payload})


# --- Torrents -----------------------------------------------------------------


def test_an_ignored_orphan_torrent_is_neither_signalled_nor_cleaned(admin_client, session):
    media = movie(session)
    add_torrent(session, media, "HASH1", is_hardlinked=True)
    orphan = add_torrent(session, media, "HASH2", is_hardlinked=False)
    refresh_media_statuses(session, media)
    assert "orphelin_qbit" in statuses(session, media)[0]

    response = ignore(admin_client, media, kind="torrent", torrent_id=orphan.id, note="Seed longue durée")

    assert response.status_code == 204
    current, muted = statuses(session, media)
    assert "orphelin_qbit" not in current and not muted
    session.refresh(orphan)
    assert orphan.ignored and media.reclaimable_bytes == 0
    assert build_delete_preview(session, media).items == []
    assert _concerned_torrents("orphan_detected", [orphan]) == []
    rule = session.exec(select(IgnoreRule)).one()
    assert (rule.target, rule.note) == ("hash2", "Seed longue durée") and rule.fingerprint
    assert len(actions(session, "ignore")) == 1


def test_an_ignored_torrent_comes_back_when_its_situation_changes(admin_client, session):
    media = movie(session)
    orphan = add_torrent(session, media, is_hardlinked=False)
    refresh_media_statuses(session, media)
    ignore(admin_client, media, kind="torrent", torrent_id=orphan.id)

    # Même contenu qu'un fichier de la bibliothèque désormais : il devient
    # réparable, ce n'est plus la situation ignorée.
    session.refresh(orphan)
    orphan.repairable = True
    session.add(orphan)
    session.commit()
    refresh_media_statuses(session, media)

    assert "non_hardlink" in statuses(session, media)[0]
    assert session.exec(select(IgnoreRule)).all() == []
    [expired] = actions(session, "ignore_expired")
    assert "de nouveau signalé" in expired.details_json


def test_an_ignored_torrent_is_forgotten_once_protected(admin_client, session):
    media = movie(session)
    orphan = add_torrent(session, media, is_hardlinked=False)
    refresh_media_statuses(session, media)
    ignore(admin_client, media, kind="torrent", torrent_id=orphan.id)

    session.refresh(orphan)
    orphan.is_hardlinked = True
    session.add(orphan)
    session.commit()
    refresh_media_statuses(session, media)

    assert session.exec(select(IgnoreRule)).all() == []
    assert "situation résolue" in actions(session, "ignore_expired")[0].details_json


def test_a_torrent_that_cannot_be_evaluated_keeps_its_rule(admin_client, session):
    """Disque démonté : l'état du torrent est inconnu. La règle n'est ni
    confirmée ni retirée — sinon toutes les alertes masquées reviendraient."""
    media = movie(session)
    orphan = add_torrent(session, media, is_hardlinked=False)
    refresh_media_statuses(session, media)
    ignore(admin_client, media, kind="torrent", torrent_id=orphan.id)

    session.refresh(orphan)
    orphan.is_hardlinked = None
    session.add(orphan)
    session.commit()
    refresh_media_statuses(session, media)

    session.refresh(orphan)
    assert orphan.ignored and len(session.exec(select(IgnoreRule)).all()) == 1


def test_an_ignored_repairable_torrent_still_seeds_the_media(admin_client, session, settings):
    media = movie(session)
    copy = add_torrent(session, media, is_hardlinked=False, repairable=True)
    refresh_media_statuses(session, media)

    ignore(admin_client, media, kind="torrent", torrent_id=copy.id)

    current, _ = statuses(session, media)
    assert "non_hardlink" not in current and "manquant_qbit" not in current
    assert asyncio.run(build_repair_preview(session, media, settings)).items == []


# --- Fichiers gardés volontairement -----------------------------------------


def test_a_kept_file_is_no_longer_a_duplicate(admin_client, session):
    media = movie(session)
    add_file(session, media, "/lib/Matrix.VF.mkv", 1, is_current=True)
    vostfr = add_file(session, media, "/lib/Matrix.VOSTFR.mkv", 2)
    refresh_media_statuses(session, media)
    assert "doublon" in statuses(session, media)[0]

    assert ignore(admin_client, media, kind="file", file_id=vostfr.id).status_code == 204

    assert "doublon" not in statuses(session, media)[0]
    assert build_delete_preview(session, media).items == []
    # L'autre fichier ne forme plus de doublon : rien à garder de ce côté.
    files = {f["path"]: f["ignorable"] for f in admin_client.get(f"/api/media/{media.id}").json()["files"]}
    assert files == {"/lib/Matrix.VF.mkv": False, "/lib/Matrix.VOSTFR.mkv": False}

    # Un troisième fichier arrive (upgrade) : lui n'est pas gardé.
    add_file(session, media, "/lib/Matrix.2160p.mkv", 3)
    refresh_media_statuses(session, media)
    assert "doublon" in statuses(session, media)[0]
    assert [i.label for i in build_delete_preview(session, media).items] == ["/lib/Matrix.2160p.mkv"]


def test_a_kept_file_that_changes_is_signalled_again(admin_client, session):
    media = movie(session)
    add_file(session, media, "/lib/Matrix.VF.mkv", 1, is_current=True)
    vostfr = add_file(session, media, "/lib/Matrix.VOSTFR.mkv", 2)
    refresh_media_statuses(session, media)
    ignore(admin_client, media, kind="file", file_id=vostfr.id)

    session.refresh(vostfr)
    vostfr.size = 99  # remplacé par un autre fichier au même chemin
    session.add(vostfr)
    session.commit()
    refresh_media_statuses(session, media)

    assert "doublon" in statuses(session, media)[0]
    assert session.exec(select(IgnoreRule)).all() == []


# --- Alertes masquées ---------------------------------------------------------


def test_a_muted_alert_leaves_a_healthy_media(admin_client, session):
    """Cas réel : un média de la bibliothèque qu'aucun Radarr ne connaît."""
    media = Media(media_type=MediaType.movie, title="Film maison", emby_item_id="lib-9")
    session.add(media)
    session.commit()
    add_torrent(session, media, is_hardlinked=True)
    refresh_media_statuses(session, media)
    assert statuses(session, media)[0] >= {"manquant_arr"}

    assert ignore(admin_client, media, kind="status", statuses=["manquant_arr"]).status_code == 204

    current, muted = statuses(session, media)
    assert "manquant_arr" not in current and muted == {"manquant_arr"}
    listed = admin_client.get("/api/media", params={"health": "sain"}).json()["items"]
    assert [m["id"] for m in listed] == [media.id] and listed[0]["muted_statuses"] == ["manquant_arr"]
    assert [m["id"] for m in admin_client.get("/api/media", params={"health": "masque"}).json()["items"]] == [media.id]
    detail = admin_client.get(f"/api/media/{media.id}").json()
    assert [r["status"] for r in detail["muted_rules"]] == ["manquant_arr"]


def test_a_muted_alert_comes_back_when_its_cause_changes(admin_client, session):
    media = movie(session)
    add_torrent(session, media, "OLD1", is_hardlinked=False)
    refresh_media_statuses(session, media)
    ignore(admin_client, media, kind="status", statuses=["orphelin_qbit"])
    assert statuses(session, media)[1] == {"orphelin_qbit"}
    assert media.reclaimable_bytes == 0  # l'espace d'une alerte masquée ne compte plus

    add_torrent(session, media, "OLD2", is_hardlinked=False)  # un autre orphelin
    refresh_media_statuses(session, media)

    current, muted = statuses(session, media)
    assert "orphelin_qbit" in current and not muted


def test_unignoring_brings_the_alert_back_at_once(admin_client, session):
    media = movie(session)
    orphan = add_torrent(session, media, is_hardlinked=False)
    refresh_media_statuses(session, media)
    ignore(admin_client, media, kind="torrent", torrent_id=orphan.id)
    rule_id = session.exec(select(IgnoreRule)).one().id

    listed = admin_client.get("/api/ignores").json()
    assert [(r["kind"], r["label"], r["present"], r["media_id"]) for r in listed] == [
        ("torrent", "Matrix.AAAA", True, media.id)
    ]
    assert admin_client.delete(f"/api/ignores/{rule_id}").status_code == 204

    assert "orphelin_qbit" in statuses(session, media)[0]
    assert len(actions(session, "unignore")) == 1
    assert admin_client.delete(f"/api/ignores/{rule_id}").status_code == 404


# --- Validation ---------------------------------------------------------------


def test_only_what_raises_an_alert_on_this_media_can_be_ignored(admin_client, session):
    media = movie(session)
    other = Media(media_type=MediaType.movie, title="Autre", radarr_id=2)
    session.add(other)
    session.commit()
    protected = add_torrent(session, media, "SAFE", is_hardlinked=True)
    foreign = add_torrent(session, other, "FOREIGN", is_hardlinked=False)
    single = add_file(session, media, "/lib/Matrix.mkv", 1, is_current=True)
    refresh_media_statuses(session, media)

    refused = [
        ignore(admin_client, media, kind="torrent", torrent_id=protected.id),
        ignore(admin_client, media, kind="torrent", torrent_id=foreign.id),
        ignore(admin_client, media, kind="file", file_id=single.id),
        ignore(admin_client, media, kind="status", statuses=["doublon"]),  # le média ne le porte pas
        ignore(admin_client, media, kind="status", statuses=["cross_seed"]),  # information, pas alerte
        ignore(admin_client, media, kind="status", statuses=[]),
    ]

    assert [r.status_code for r in refused] == [400] * len(refused)
    assert ignore(admin_client, media, kind="nimporte").status_code == 422
    assert session.exec(select(IgnoreRule)).all() == []


def test_the_same_element_is_never_ignored_twice(admin_client, session):
    media = Media(media_type=MediaType.movie, title="Film maison", emby_item_id="lib-9")
    session.add(media)
    session.commit()
    refresh_media_statuses(session, media)

    assert ignore(admin_client, media, kind="status", statuses=["manquant_arr"]).status_code == 204
    assert ignore(admin_client, media, kind="status", statuses=["manquant_arr"]).status_code == 400


# --- Scan complet -------------------------------------------------------------


def test_the_full_scan_applies_and_records_the_rules(session):
    """Le scan complet reconstruit tout le cache : les règles, elles, restent,
    et leur situation est relevée au premier passage."""
    media = movie(session)
    session.add(
        IgnoreRule(
            kind="torrent",
            media_key=media_key(media),
            media_title="Matrix",
            media_type="movie",
            target="aaaa",
            label="Matrix.AAAA",
        )
    )
    session.commit()
    rebuilt = Media(media_type=MediaType.movie, title="Matrix", radarr_id=1, emby_item_id="e1")
    orphan = Torrent(hash="AAAA", name="Matrix.AAAA", is_hardlinked=False)

    ignores = IgnoreSet.load(session)
    apply_statuses(rebuilt, [], [orphan], [], ignores)
    ignores.persist(session)

    assert orphan.ignored and "orphelin_qbit" not in rebuilt.statuses
    assert session.exec(select(IgnoreRule)).one().fingerprint


def test_a_rule_whose_media_disappeared_stays_listed(admin_client, session):
    session.add(
        IgnoreRule(
            kind="status",
            media_key="movie:arr:0:77",
            media_title="Parti",
            media_type="movie",
            target="manquant_qbit",
            label="Non seedé",
        )
    )
    session.commit()

    [listed] = admin_client.get("/api/ignores").json()

    assert (listed["present"], listed["media_id"], listed["status"]) == (False, None, "manquant_qbit")


def test_history_mentions_every_ignore_action(admin_client, session):
    media = movie(session)
    orphan = add_torrent(session, media, is_hardlinked=False)
    refresh_media_statuses(session, media)
    ignore(admin_client, media, kind="torrent", torrent_id=orphan.id)

    history = admin_client.get("/api/history").json()

    assert history[0]["action"] == "ignore" and history[0]["details"][0]["label"] == "Torrent Matrix.AAAA"
