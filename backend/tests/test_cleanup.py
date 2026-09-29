"""Assistant de nettoyage (roadmap Phase 2) : protections, score explicable
et déterministe, espace réellement libérable, classement, routes et
performance sur 10 000 médias."""

import asyncio
import json
import os
import time
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest
from sqlmodel import Session, select

from app.models.ignore import IgnoreRule
from app.models.media import EmbyUser, Media, MediaFile, MediaRequest, MediaType, MediaWatch, Torrent, TorrentFile
from app.models.settings import Settings
from app.schemas.cleanup import CleanupSettings, CleanupWeights
from app.services.cleanup import CandidateQuery, candidate_detail, candidates_page, collect_facts
from app.services.cleanup_score import (
    IN_PROGRESS_FACTOR,
    PRESETS,
    CandidateFacts,
    evaluate,
    rank_value,
    ranked,
    space_scale,
)
from app.services.disk_footprint import whole_media_bytes
from app.services.media_delete import build_delete_footprint, reclaimed_bytes
from app.services.media_status import refresh_media_statuses
from app.services.seed_protection import SeedObligation

NOW = datetime(2026, 9, 29, 12, tzinfo=UTC)
BALANCED = PRESETS["balanced"]


def facts(**fields) -> CandidateFacts:
    base = {
        "media_id": 1,
        "media_type": "movie",
        "title": "Film",
        "year": 2020,
        "reclaimable_bytes": 10 * 1024**3,
        "date_added": NOW - timedelta(days=400),
        "last_played_at": NOW - timedelta(days=200),
    }
    return CandidateFacts(**(base | fields))


# --- Score pur ------------------------------------------------------------------------


def component(result, key):
    return next(c for c in result.components if c.key == key)


def test_disinterest_grows_with_the_days_without_playback():
    assert component(evaluate(facts(last_played_at=NOW), BALANCED, NOW), "disinterest").value == 0
    half = evaluate(facts(last_played_at=NOW - timedelta(days=182)), BALANCED, NOW)
    assert component(half, "disinterest").value == 50
    assert component(half, "disinterest").since == "last_played"
    full = evaluate(facts(last_played_at=NOW - timedelta(days=900)), BALANCED, NOW)
    assert component(full, "disinterest").value == 100


def test_never_watched_counts_from_the_date_added():
    never = component(evaluate(facts(last_played_at=None), BALANCED, NOW), "disinterest")
    assert (never.value, never.since, never.days) == (100, "added", 400)


def test_an_unknown_date_never_implies_disinterest():
    unknown = component(evaluate(facts(last_played_at=None, date_added=None), BALANCED, NOW), "disinterest")
    assert (unknown.value, unknown.since, unknown.days) == (0, None, None)


def test_remaining_potential_counts_active_users_who_did_not_finish():
    assert component(evaluate(facts(active_users=0), BALANCED, NOW), "potential").value == 100
    half = component(evaluate(facts(active_users=4, active_unfinished=2), BALANCED, NOW), "potential")
    assert (half.value, half.users, half.unfinished) == (50, 4, 2)
    assert component(evaluate(facts(active_users=3, active_unfinished=3), BALANCED, NOW), "potential").value == 0


@pytest.mark.parametrize(("status", "value"), [("ended", 100), ("deleted", 100), ("continuing", 30), ("upcoming", 0)])
def test_an_ended_series_scores_higher_than_a_running_one(status, value):
    result = evaluate(facts(media_type="series", series_status=status), BALANCED, NOW)
    assert component(result, "series").value == value


def test_weights_are_shared_again_when_a_component_does_not_apply():
    movie = evaluate(facts(), BALANCED, NOW)
    assert "series" not in {c.key for c in movie.components}
    assert sum(c.weight for c in movie.components) == 100
    series = evaluate(facts(media_type="series", series_status="ended"), BALANCED, NOW)
    assert [c.weight for c in series.components] == [35, 35, 15, 15]


def test_someone_watching_right_now_cuts_the_score():
    idle = evaluate(facts(), BALANCED, NOW)
    watching = evaluate(facts(in_progress_names=("Marie",)), BALANCED, NOW)
    assert watching.raw_score == idle.raw_score
    assert watching.score == round(idle.raw_score * IN_PROGRESS_FACTOR)


def test_the_score_is_deterministic():
    one = evaluate(facts(active_users=3, active_unfinished=1), BALANCED, NOW)
    two = evaluate(facts(active_users=3, active_unfinished=1), BALANCED, NOW)
    assert (one.score, [c.read() for c in one.components]) == (two.score, [c.read() for c in two.components])


def test_a_tie_on_the_main_reason_prefers_disinterest():
    # Désintérêt et potentiel restant à 100, même poids.
    tie = evaluate(facts(last_played_at=NOW - timedelta(days=900)), BALANCED, NOW)
    assert tie.main_reason == "disinterest"


def test_the_main_reason_is_the_heaviest_component():
    result = evaluate(
        facts(active_users=2, active_unfinished=2, last_played_at=NOW - timedelta(days=900)), BALANCED, NOW
    )
    assert result.main_reason == "disinterest"


# --- Protections ----------------------------------------------------------------------


def kinds(result) -> list[str]:
    return [p.kind for p in result.protections]


@pytest.mark.parametrize(
    ("fields", "expected"),
    [
        ({"favorite_names": ("Paul",)}, "favorite"),
        ({"unwatched_requesters": ("Léa",)}, "request"),
        ({"unknown_requester": True}, "request_unknown"),
        ({"date_added": NOW - timedelta(days=10)}, "recent"),
        ({"excluded": True}, "excluded"),
        (
            {"seed_obligation": SeedObligation("min_seed", NOW + timedelta(days=3), "t.example", 14)},
            "seed",
        ),
    ],
)
def test_each_protection_keeps_a_media_out_of_the_ranking(fields, expected):
    result = evaluate(facts(**fields), BALANCED, NOW)

    assert kinds(result) == [expected]
    assert ranked([result], BALANCED) == []


def test_the_recent_protection_ends_on_its_threshold():
    assert kinds(evaluate(facts(date_added=NOW - timedelta(days=29)), BALANCED, NOW)) == ["recent"]
    assert kinds(evaluate(facts(date_added=NOW - timedelta(days=30)), BALANCED, NOW)) == []


# --- Classement -----------------------------------------------------------------------


def test_the_space_scale_is_logarithmic():
    assert space_scale(0, 100) == 0
    assert space_scale(100, 100) == 100
    # Un fichier dix fois plus petit garde bien plus d'un dixième de l'échelle.
    assert space_scale(10 * 1024**3, 100 * 1024**3) > 80


def test_the_space_priority_moves_the_ranking():
    old_small = evaluate(
        facts(media_id=1, title="Vieux", reclaimable_bytes=1024**2, last_played_at=None), BALANCED, NOW
    )
    big_recent = evaluate(
        facts(media_id=2, title="Gros", reclaimable_bytes=80 * 1024**3, last_played_at=NOW - timedelta(days=40)),
        BALANCED,
        NOW,
    )
    score_only = BALANCED.model_copy(update={"space_priority": 0})
    space_only = BALANCED.model_copy(update={"space_priority": 100})

    assert [e.facts.title for e, _ in ranked([old_small, big_recent], score_only)] == ["Vieux", "Gros"]
    assert [e.facts.title for e, _ in ranked([old_small, big_recent], space_only)] == ["Gros", "Vieux"]
    assert rank_value(60, 0, 0, 0) == 60


def test_ties_are_broken_the_same_way_every_time():
    same = [evaluate(replace(facts(), media_id=i, title=f"Film {i % 3}"), BALANCED, NOW) for i in range(6)]
    first = [e.facts.media_id for e, _ in ranked(same, BALANCED)]
    assert first == [e.facts.media_id for e, _ in ranked(list(reversed(same)), BALANCED)]


def test_presets_are_valid_settings():
    for name, preset in PRESETS.items():
        assert CleanupSettings.model_validate(preset.model_dump()).preset == name
    assert (
        PRESETS["prudent"].space_priority < PRESETS["balanced"].space_priority < PRESETS["space_first"].space_priority
    )


def test_all_weights_at_zero_are_refused():
    with pytest.raises(ValueError):
        CleanupWeights(disinterest=0, potential=0, age=0, series=0)


# --- Collecte depuis la base --------------------------------------------------------


def _media(session: Session, title="Film", **fields) -> Media:
    media = Media(
        media_type=fields.pop("media_type", MediaType.movie),
        title=title,
        total_size=fields.pop("total_size", 1000),
        full_reclaimable_bytes=fields.pop("full_reclaimable_bytes", 1000),
        emby_date_added=fields.pop("emby_date_added", datetime.now(UTC) - timedelta(days=400)),
        **fields,
    )
    session.add(media)
    session.commit()
    return media


def _user(session: Session, user_id: str, *, active_days_ago=1, disabled=False) -> None:
    session.add(
        EmbyUser(
            id=user_id,
            name=user_id.capitalize(),
            is_disabled=disabled,
            last_activity_at=None if active_days_ago is None else datetime.now(UTC) - timedelta(days=active_days_ago),
        )
    )
    session.commit()


def _watch(session: Session, media: Media, user_id: str, **fields) -> None:
    session.add(MediaWatch(media_id=media.id, emby_user_id=user_id, **fields))
    session.commit()


def only(session: Session) -> CandidateFacts:
    [found], _ = collect_facts(session)
    return found


def test_only_active_accounts_count(session, settings):
    media = _media(session)
    _user(session, "marie")
    _user(session, "ancien", active_days_ago=200)
    _user(session, "jamais", active_days_ago=None)
    _user(session, "coupe", disabled=True)
    _user(session, "exclu")
    settings.excluded_emby_user_ids = json.dumps(["exclu"])
    session.add(settings)
    session.commit()
    for user in ("marie", "ancien", "jamais", "coupe", "exclu"):
        _watch(session, media, user, played=False, favorite=True, in_progress=True)

    found = only(session)

    assert (found.active_users, found.active_unfinished) == (1, 1)
    assert found.favorite_names == ("Marie",) and found.in_progress_names == ("Marie",)


def _seer(session: Session, settings: Settings) -> None:
    settings.seer_enabled = True
    settings.seer_url = "http://seer"
    settings.seer_api_key = "k"
    session.add(settings)
    session.commit()


def _request(session: Session, media: Media, emby_id: str | None, status="approved", name="Léa") -> None:
    session.add(
        MediaRequest(
            media_id=media.id,
            seer_request_id=len(session.exec(select(MediaRequest)).all()) + 1,
            status=status,
            requested_by_name=name,
            requested_by_emby_id=emby_id,
        )
    )
    session.commit()


def test_a_request_protects_until_its_requester_has_seen_it(session, settings):
    _seer(session, settings)
    media = _media(session)
    _user(session, "lea")
    _request(session, media, "lea")
    assert only(session).unwatched_requesters == ("Léa",)

    _watch(session, media, "lea", played=True)
    assert only(session).unwatched_requesters == ()


def test_an_unidentified_requester_protects_by_caution(session, settings):
    _seer(session, settings)
    media = _media(session)
    _request(session, media, None, name="Inconnu")

    assert only(session).unknown_requester is True


def test_a_declined_request_or_a_disabled_seer_protects_nothing(session, settings):
    media = _media(session)
    _request(session, media, None)
    assert only(session).unknown_requester is False  # Seer désactivé : aucune trace

    _seer(session, settings)
    session.exec(select(MediaRequest)).one().status = "declined"
    session.commit()
    assert only(session).unknown_requester is False


def test_a_seed_obligation_protects_the_whole_media(session, settings):
    settings.seed_protection_enabled = True
    session.add(settings)
    media = _media(session)
    session.add(Torrent(media_id=media.id, hash="h", name="t", is_private=True, completed_on=datetime.now(UTC)))
    session.commit()

    obligation = only(session).seed_obligation
    assert obligation is not None and obligation.reason == "min_seed"


def test_media_with_nothing_on_disk_are_not_candidates(session, settings):
    _media(session, total_size=0, full_reclaimable_bytes=0)
    assert collect_facts(session)[0] == []


def test_a_manual_exclusion_is_an_ignore_rule_that_never_expires(admin_client, session, settings, tmp_path):
    media = _media(session, radarr_id=7)
    video = tmp_path / "Film.mkv"
    video.write_bytes(b"x" * 1000)
    session.add(MediaFile(media_id=media.id, path=str(video), size=1000, is_current=True))
    session.commit()

    response = admin_client.post("/api/ignores", json={"media_id": media.id, "kind": "cleanup", "note": "Classique"})

    assert response.status_code == 204, response.text
    assert only(session).excluded is True
    # Un recalcul des statuts (scan, action) ne retire jamais cette règle.
    refresh_media_statuses(session, media)
    assert session.exec(select(IgnoreRule)).one().kind == "cleanup"
    [listed] = admin_client.get("/api/ignores").json()
    assert (listed["kind"], listed["media_id"], listed["note"]) == ("cleanup", media.id, "Classique")
    assert admin_client.get(f"/api/media/{media.id}").json()["cleanup_rule_id"] == listed["id"]
    assert admin_client.post("/api/ignores", json={"media_id": media.id, "kind": "cleanup"}).status_code == 400


# --- Espace réellement libérable ------------------------------------------------------


def test_whole_media_space_matches_the_delete_dialog(session, settings, tmp_path):
    """Même calcul que le dialogue « Supprimer » : le fichier hardlinké avec
    son torrent compte une fois, celui dont un lien vit ailleurs zéro."""
    library = tmp_path / "Film.mkv"
    library.write_bytes(b"x" * 1000)
    seeded = tmp_path / "torrents" / "Film.mkv"
    seeded.parent.mkdir()
    os.link(library, seeded)
    extra = tmp_path / "Film.bonus.mkv"
    extra.write_bytes(b"y" * 300)
    os.link(extra, tmp_path / "ailleurs.mkv")  # lien hors du média
    media = _media(session)
    session.add_all(
        [
            MediaFile(media_id=media.id, path=str(library), size=1000),
            MediaFile(media_id=media.id, path=str(extra), size=300),
            Torrent(media_id=media.id, hash="h", name="Film", save_path=str(seeded.parent), size=1000),
            TorrentFile(torrent_hash="h", path=str(seeded), size=1000),
        ]
    )
    session.commit()

    refresh_media_statuses(session, media)
    settings.qbittorrent_url = None
    live = asyncio.run(build_delete_footprint(session, media, settings))

    assert media.full_reclaimable_bytes == 1000
    assert media.full_reclaimable_bytes == reclaimed_bytes(
        live, [t.id for t in live.torrents], [f.id for f in live.files]
    )
    assert whole_media_bytes(live) == 1000


def test_a_protected_orphan_no_longer_counts_as_reclaimable(session, settings):
    media = _media(session, statuses="orphelin_qbit")
    session.add(Torrent(media_id=media.id, hash="h", name="t", size=500, is_hardlinked=False, is_private=True))
    session.commit()

    refresh_media_statuses(session, media)
    assert media.reclaimable_bytes == 500

    settings.seed_protection_enabled = True
    session.add(settings)
    session.commit()
    refresh_media_statuses(session, media)
    assert "orphelin_qbit" in media.statuses and media.reclaimable_bytes == 0


# --- Routes -------------------------------------------------------------------------


def test_the_page_ranks_candidates_and_lists_protected_media_apart(admin_client, session, settings):
    forgotten = _media(
        session, "Oublié", full_reclaimable_bytes=5000, last_played_at=datetime.now(UTC) - timedelta(days=700)
    )
    # Un compte actif ne l'a pas fini : potentiel restant nul, le désintérêt
    # est la seule raison.
    _user(session, "marie")
    _watch(session, forgotten, "marie", played=False)
    _media(session, "Récent", emby_date_added=datetime.now(UTC) - timedelta(days=2))
    _media(session, "Regardé", last_played_at=datetime.now(UTC) - timedelta(days=1))

    page = admin_client.get("/api/cleanup/candidates").json()
    assert [item["title"] for item in page["items"]] == ["Oublié", "Regardé"]
    assert (page["candidate_count"], page["protected_count"], page["total_reclaimable_bytes"]) == (2, 1, 6000)

    everything = admin_client.get("/api/cleanup/candidates?include_protected=true").json()
    assert everything["items"][-1]["title"] == "Récent"
    assert everything["items"][-1]["protections"][0]["kind"] == "recent"

    detail = admin_client.get(f"/api/cleanup/candidates/{page['items'][0]['media_id']}").json()
    assert {c["key"] for c in detail["components"]} == {"disinterest", "potential", "age"}
    assert detail["main_reason"] == "disinterest"


def test_filters_and_pagination(admin_client, session, settings):
    for i in range(5):
        _media(session, f"Film {i}")
    _media(session, "Série", media_type=MediaType.series)

    assert admin_client.get("/api/cleanup/candidates?media_type=series").json()["total"] == 1
    assert admin_client.get("/api/cleanup/candidates?search=film%203").json()["items"][0]["title"] == "Film 3"
    page = admin_client.get("/api/cleanup/candidates?sort=title&page=2&page_size=2").json()
    assert [item["title"] for item in page["items"]] == ["Film 2", "Film 3"]


@pytest.mark.parametrize(
    "query", ["page=0", "page_size=101", "sort=hasard", "media_type=music", "min_score=101", "search=" + "x" * 101]
)
def test_the_query_is_bounded(admin_client, query):
    assert admin_client.get(f"/api/cleanup/candidates?{query}").status_code == 422


def test_an_unknown_candidate_is_a_404(admin_client):
    assert admin_client.get("/api/cleanup/candidates/999").status_code == 404


def test_settings_are_validated_and_stored(admin_client, session, settings):
    body = admin_client.get("/api/cleanup/settings").json()
    assert body["settings"] == BALANCED.model_dump()
    assert set(body["presets"]) == {"prudent", "balanced", "space_first"}

    custom = PRESETS["space_first"].model_dump() | {"preset": None, "space_priority": 55}
    assert admin_client.put("/api/cleanup/settings", json=custom).json()["settings"]["space_priority"] == 55
    for invalid in ({"disinterest_days": 10}, {"space_priority": 101}, {"recent_days": -1}):
        assert admin_client.put("/api/cleanup/settings", json=custom | invalid).status_code == 422


def test_unreadable_settings_fall_back_to_the_balanced_preset(session, settings):
    settings.cleanup_settings = "pas du json"
    session.add(settings)
    session.commit()
    assert collect_facts(session)[1] == BALANCED


def test_the_routes_require_a_session(client):
    for path in ("/api/cleanup/candidates", "/api/cleanup/candidates/1", "/api/cleanup/settings"):
        assert client.get(path).status_code == 401
    assert client.put("/api/cleanup/settings", json=BALANCED.model_dump()).status_code == 401


# --- Performance ------------------------------------------------------------------


def test_ten_thousand_media_are_ranked_under_half_a_second(session, settings):
    settings.seed_protection_enabled = True
    session.add(settings)
    now = datetime.now(UTC)
    for u in range(8):
        session.add(EmbyUser(id=f"u{u}", name=f"User {u}", last_activity_at=now - timedelta(days=u * 20)))
    medias = [
        Media(
            media_type=MediaType.series if i % 3 == 0 else MediaType.movie,
            title=f"Média {i}",
            total_size=1_000_000 * (i % 500 + 1),
            full_reclaimable_bytes=1_000_000 * (i % 500 + 1),
            emby_date_added=now - timedelta(days=i % 900),
            last_played_at=now - timedelta(days=i % 400) if i % 4 else None,
            series_status="ended" if i % 2 else "continuing",
        )
        for i in range(10_000)
    ]
    session.add_all(medias)
    session.commit()
    ids = [m.id for m in medias]
    session.add_all(
        MediaWatch(media_id=mid, emby_user_id=f"u{u}", played=(mid + u) % 3 == 0, favorite=(mid + u) % 97 == 0)
        for mid in ids
        for u in range(8)
    )
    session.add_all(
        Torrent(
            media_id=mid,
            hash=f"h{mid}",
            name="t",
            is_private=mid % 2 == 0,
            completed_on=now - timedelta(days=mid % 60),
            trackers_json='[{"domain": "tracker.example.org", "status": "ok"}]',
        )
        for mid in ids
    )
    session.commit()

    timings = []
    for _ in range(3):
        started = time.monotonic()
        page = candidates_page(session, CandidateQuery())
        timings.append(time.monotonic() - started)

    assert page.candidate_count + page.protected_count == 10_000
    assert min(timings) < 0.5, [f"{t:.2f}" for t in timings]
    first = page.items[0]
    assert candidate_detail(session, first.media_id) is not None
