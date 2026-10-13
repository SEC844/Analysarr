"""Automatisation fondée sur l'assistant de nettoyage (roadmap Phase 5) :
score ET espace minimum avec planchers, plafond, protections revérifiées,
simulation, historique, et refus d'agir quand le visionnage ou les demandes
n'ont pas pu être lus au dernier scan complet."""

import asyncio
import json
from datetime import UTC, datetime, timedelta
from typing import cast

import httpx
import pytest
from sqlmodel import Session, select

from app.clients.emby import EmbyClient
from app.models.activity import ActionLog
from app.models.automation import Automation
from app.models.media import EmbyUser, Media, MediaFile, MediaType, MediaWatch, ScanRun, ScanStatus, Torrent
from app.schemas.media import DeleteStepResult, MediaDeleteFootprint, MediaDeleteSelection, MediaDeleteSelectionResult
from app.services import automations
from app.services.automations import (
    MAX_MEDIA_DELETIONS,
    as_rule,
    eligible_medias,
    run_rule,
    source_blocker,
)
from app.services.scan import collect

AUTOMATIONS = "/api/automations"
GB = 1024**3


def _scan(session: Session, failed: str = "") -> None:
    session.add(ScanRun(status=ScanStatus.completed, scope="full", failed_sources=failed))
    session.commit()


def _media(session: Session, title: str, size: int = 50 * GB, added_days_ago: int = 400, **fields) -> Media:
    media = Media(
        media_type=fields.pop("media_type", MediaType.movie),
        title=title,
        total_size=size,
        full_reclaimable_bytes=size,
        emby_date_added=datetime.now(UTC) - timedelta(days=added_days_ago),
        radarr_id=fields.pop("radarr_id", None),
        **fields,
    )
    session.add(media)
    session.commit()
    session.refresh(media)
    return media


def _rule(session: Session, **overrides) -> Automation:
    fields = {
        "name": "Grand ménage",
        "trigger": "cleanup_candidate",
        "action": "delete_media",
        "conditions": json.dumps({"min_score": 60, "min_reclaimable_bytes": 10 * GB}),
        "max_actions": 5,
        "dry_run": False,
    } | overrides
    automation = Automation(**fields)
    session.add(automation)
    session.commit()
    session.refresh(automation)
    return automation


def _titles(session: Session, automation: Automation) -> list[str]:
    return [media.title for media, _ in eligible_medias(session, as_rule(automation))]


# --- Validation des règles ----------------------------------------------------


def _payload(**overrides) -> dict:
    return {
        "name": "Grand ménage",
        "trigger": "cleanup_candidate",
        "action": "delete_media",
        "conditions": {"min_score": 70, "min_reclaimable_bytes": 20 * GB, "min_ratio": 2},
        "max_actions": 3,
        "dry_run": True,
    } | overrides


def test_a_valid_rule_keeps_only_its_own_conditions(admin_client):
    response = admin_client.post(AUTOMATIONS, json=_payload())
    assert response.status_code == 201
    conditions = response.json()["conditions"]
    assert conditions["min_score"] == 70 and conditions["min_reclaimable_bytes"] == 20 * GB
    assert conditions["min_ratio"] is None  # sans objet pour ce déclencheur


@pytest.mark.parametrize(
    "overrides",
    [
        {"conditions": {"min_reclaimable_bytes": 20 * GB}},  # score manquant
        {"conditions": {"min_score": 40, "min_reclaimable_bytes": 20 * GB}},  # sous le plancher
        {"conditions": {"min_score": 70}},  # espace manquant
        {"conditions": {"min_score": 70, "min_reclaimable_bytes": GB // 2}},  # sous 1 Go
        {"max_actions": MAX_MEDIA_DELETIONS + 1},
        {"action": "cleanup"},  # nettoyage cascade : réservé aux statuts du scan
        {"trigger": "orphan_detected"},  # suppression de médias entiers ailleurs
    ],
)
def test_risky_rules_are_refused(admin_client, overrides):
    assert admin_client.post(AUTOMATIONS, json=_payload(**overrides)).status_code == 400


def test_a_candidate_can_simply_be_notified(admin_client):
    response = admin_client.post(AUTOMATIONS, json=_payload(action="notify_only", max_actions=50))
    assert response.status_code == 201


def test_other_triggers_drop_the_score_condition(admin_client):
    payload = {
        "name": "Orphelins",
        "trigger": "orphan_detected",
        "action": "cleanup",
        "conditions": {"min_score": 80},
    }
    assert admin_client.post(AUTOMATIONS, json=payload).json()["conditions"]["min_score"] is None


# --- Sélection ----------------------------------------------------------------


def test_candidates_follow_score_space_and_protections(session, settings):
    _media(session, "Oublié", size=50 * GB)
    _media(session, "Petit", size=2 * GB)
    _media(session, "Récent", size=50 * GB, added_days_ago=3)  # protégé
    series = _media(session, "Série", size=40 * GB, media_type=MediaType.series)
    watched = _media(session, "En cours", size=60 * GB)
    session.add(EmbyUser(id="u1", name="Lea", last_activity_at=datetime.now(UTC)))
    session.add(MediaWatch(media_id=watched.id, emby_user_id="u1", in_progress=True, progress=30))
    session.commit()

    automation = _rule(session)
    assert sorted(_titles(session, automation)) == ["Oublié", "Série"]

    automation.conditions = json.dumps({"min_score": 60, "min_reclaimable_bytes": 10 * GB, "media_types": ["movie"]})
    assert _titles(session, automation) == ["Oublié"]
    assert series.id is not None


def test_the_floors_hold_even_for_a_rule_stored_below_them(session, settings):
    _media(session, "Petit", size=GB // 2)
    automation = _rule(session, conditions=json.dumps({"min_score": 0, "min_reclaimable_bytes": 0}))
    assert _titles(session, automation) == []
    # Conditions illisibles : rien plutôt que tout.
    assert _titles(session, _rule(session, conditions="pas du json")) == []


# --- Sources illisibles ---------------------------------------------------------


def test_nothing_happens_before_a_full_scan_or_after_unreadable_sources(session, settings):
    assert source_blocker(session) is not None
    _scan(session, failed="watch")
    assert "Visionnage" in (source_blocker(session) or "")
    _scan(session, failed="requests")
    assert "Demandes" in (source_blocker(session) or "")
    _scan(session)
    assert source_blocker(session) is None


def test_a_blocked_rule_deletes_nothing_and_says_why(session, settings, monkeypatch):
    _media(session, "Oublié")
    _scan(session, failed="watch")
    called = []
    monkeypatch.setattr(automations, "execute_media_delete", lambda *a, **k: called.append(a))

    result = asyncio.run(run_rule(session, settings, [], _rule(session)))

    assert called == [] and result.executed == 0
    assert result.steps[0].success is False and "Visionnage" in (result.steps[0].error or "")
    assert session.exec(select(ActionLog)).first() is not None


def test_the_scan_records_unreadable_sources(session, settings):
    class BrokenServer:
        async def get_users(self):
            raise httpx.ConnectError("injoignable")

    users, ok = asyncio.run(collect._apply_watch_stats(settings, cast(EmbyClient, BrokenServer()), []))
    assert (users, ok) == ([], False)


def test_a_readable_media_server_is_not_flagged(session, settings, fake_http):
    fake_http["http://emby"] = lambda req: httpx.Response(200, json=[] if "Users" in req.url.path else {"Items": []})
    client = collect.media_server_client(settings)
    assert client is not None
    _users, ok = asyncio.run(collect._apply_watch_stats(settings, client, []))
    assert ok is True


# --- Exécution ------------------------------------------------------------------


@pytest.fixture
def deletions(monkeypatch):
    """Remplace la suppression réelle (testée ailleurs) : on vérifie ce que
    l'automatisation lui demande."""
    calls: list[tuple[int, MediaDeleteSelection]] = []

    async def footprint(session, media, settings):
        return MediaDeleteFootprint(units=[], torrents=[], files=[])

    async def execute(session, media, settings, selection):
        calls.append((media.id, selection))
        return MediaDeleteSelectionResult(
            steps=[DeleteStepResult(kind="library_file", label="fichier", success=True)], media_deleted=True
        )

    monkeypatch.setattr(automations, "build_delete_footprint", footprint)
    monkeypatch.setattr(automations, "reclaimed_bytes", lambda fp, t, f: 50 * GB)
    monkeypatch.setattr(automations, "execute_media_delete", execute)
    return calls


def test_the_whole_media_goes_through_the_usual_deletion(session, settings, deletions):
    _scan(session)
    tracked = _media(session, "Suivi", radarr_id=7)
    untracked = _media(session, "Non suivi", statuses="manquant_arr")
    session.add(MediaFile(media_id=tracked.id, path="/media/Suivi/Suivi.mkv", size=50 * GB))
    session.add(Torrent(media_id=tracked.id, hash="abc", name="Suivi"))
    session.commit()

    result = asyncio.run(run_rule(session, settings, [], _rule(session)))

    by_media = dict(deletions)
    assert set(by_media) == {tracked.id, untracked.id}
    assert len(by_media[tracked.id].torrent_ids) == 1 and len(by_media[tracked.id].media_file_ids) == 1
    assert by_media[tracked.id].remove_from_arr is True
    assert by_media[untracked.id].remove_from_arr is False
    assert result.freed_bytes == 100 * GB and result.executed == 2
    entry = session.exec(select(ActionLog)).first()
    assert entry is not None and entry.action == "automation"


def test_the_cap_and_the_simulation_are_respected(session, settings, deletions):
    _scan(session)
    for i in range(4):
        _media(session, f"Film {i}")

    capped = asyncio.run(run_rule(session, settings, [], _rule(session, max_actions=2)))
    assert (capped.matched, capped.executed, len(deletions)) == (4, 2, 2)

    deletions.clear()
    simulated = asyncio.run(run_rule(session, settings, [], _rule(session, dry_run=True)))
    assert deletions == [] and all("simulation" in s.label for s in simulated.steps)


def test_a_media_that_became_protected_is_left_aside(session, settings, deletions):
    _scan(session)
    media = _media(session, "Oublié")
    session.add(EmbyUser(id="u1", name="Lea", last_activity_at=datetime.now(UTC)))
    session.add(MediaWatch(media_id=media.id, emby_user_id="u1", favorite=True))
    session.commit()

    steps, freed = asyncio.run(automations._delete_whole_media(session, settings, media))

    assert deletions == [] and freed == 0
    assert steps[0].success is False


def test_a_stored_rule_is_capped_at_execution(session, settings, deletions):
    _scan(session)
    for i in range(MAX_MEDIA_DELETIONS + 2):
        _media(session, f"Film {i}")
    result = asyncio.run(run_rule(session, settings, [], _rule(session, max_actions=50)))
    assert result.executed == MAX_MEDIA_DELETIONS


def test_the_preview_counts_the_whole_media(admin_client, session, settings):
    _scan(session)
    _media(session, "Oublié", size=30 * GB)
    automation = _rule(session)
    body = admin_client.get(f"{AUTOMATIONS}/{automation.id}/preview").json()
    assert body["matched"] == 1 and body["freed_bytes"] == 30 * GB


def test_the_manual_run_accepts_the_new_trigger(admin_client, session, settings, deletions):
    _scan(session)
    _media(session, "Oublié")
    automation = _rule(session, dry_run=True)
    response = admin_client.post(f"{AUTOMATIONS}/{automation.id}/run")
    assert response.status_code == 200 and response.json()["matched"] == 1


def test_the_cleanup_page_flags_unreadable_sources(admin_client, session, settings):
    _scan(session, failed="watch,requests")
    assert admin_client.get("/api/cleanup/candidates").json()["unreliable_sources"] == ["watch", "requests"]
