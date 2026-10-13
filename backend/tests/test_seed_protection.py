"""Protection du seed (services/seed_protection.py) : aucun nettoyage ni
aucune automatisation ne supprime un torrent qui n'a pas fini son temps de
seed ; « inconnu » vaut « privé » ; jamais activée en silence sur une
installation existante."""

import asyncio
import json
import logging
import time
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import text
from sqlmodel import Session

from app.database import engine, init_db
from app.models.automation import Automation
from app.models.media import Media, MediaType, Torrent
from app.models.settings import Settings
from app.schemas.seed import TrackerSeedRule
from app.services.automations import as_rule, eligible_medias
from app.services.cascade_delete import build_delete_preview, execute_delete
from app.services.seed_protection import SeedPolicy, seed_obligation, seed_policy, split_protected

NOW = datetime(2026, 9, 29, 12, tzinfo=UTC)
ROUTE = "/api/seed-protection"


def torrent(*, private=True, days_ago=10.0, ratio=0.5, trackers=("tracker.example.org",), **extra) -> Torrent:
    return Torrent(
        hash="h",
        name="Film.2026.1080p",
        is_private=private,
        ratio=ratio,
        completed_on=None if days_ago is None else (NOW - timedelta(days=days_ago)).replace(tzinfo=None),
        trackers_json=json.dumps([{"domain": d, "status": "ok"} for d in trackers]),
        **extra,
    )


POLICY = SeedPolicy(enabled=True, private_min_days=14, public_enabled=False, public_min_days=3)


# --- Règle pure ---------------------------------------------------------------------


def test_disabled_protection_never_protects():
    assert seed_obligation(torrent(days_ago=0), SeedPolicy(enabled=False), NOW) is None


def test_a_private_torrent_is_protected_until_its_minimum():
    obligation = seed_obligation(torrent(days_ago=10), POLICY, NOW)

    assert obligation is not None
    assert (obligation.reason, obligation.tracker, obligation.min_days) == ("min_seed", "tracker.example.org", 14)
    assert obligation.until == NOW + timedelta(days=4)
    assert seed_obligation(torrent(days_ago=15), POLICY, NOW) is None


def test_unknown_privacy_counts_as_private():
    assert seed_obligation(torrent(private=None, days_ago=10), POLICY, NOW) is not None


def test_public_torrents_are_free_unless_the_option_is_on():
    public = torrent(private=False, days_ago=1)
    assert seed_obligation(public, POLICY, NOW) is None

    with_public = SeedPolicy(enabled=True, public_enabled=True, public_min_days=3)
    obligation = seed_obligation(public, with_public, NOW)
    assert obligation is not None and obligation.min_days == 3
    assert seed_obligation(torrent(private=False, days_ago=4), with_public, NOW) is None


def test_a_tracker_rule_needs_both_its_days_and_its_ratio():
    policy = SeedPolicy(
        enabled=True,
        private_min_days=14,
        tracker_rules=(TrackerSeedRule(domain="example.org", min_days=30, min_ratio=1.0),),
    )

    # Sous-domaine couvert par la règle : 30 jours au lieu de 14.
    assert seed_obligation(torrent(days_ago=20, ratio=5.0), policy, NOW).reason == "min_seed"
    # Durée atteinte, ratio pas encore (ou illisible) : toujours protégé.
    assert seed_obligation(torrent(days_ago=40, ratio=0.9), policy, NOW).reason == "min_ratio"
    assert seed_obligation(torrent(days_ago=40, ratio=None), policy, NOW).reason == "min_ratio"
    assert seed_obligation(torrent(days_ago=40, ratio=1.0), policy, NOW) is None


def test_a_tracker_rule_also_covers_a_public_torrent():
    policy = SeedPolicy(enabled=True, tracker_rules=(TrackerSeedRule(domain="tracker.example.org", min_days=7),))

    assert seed_obligation(torrent(private=False, days_ago=2), policy, NOW) is not None


def test_an_unknown_date_is_protected_without_end():
    obligation = seed_obligation(torrent(days_ago=None), POLICY, NOW)

    assert obligation is not None and (obligation.reason, obligation.until) == ("unknown_date", None)


def test_the_added_date_is_the_fallback_reference():
    added = (NOW - timedelta(days=20)).replace(tzinfo=None)
    assert seed_obligation(torrent(days_ago=None, added_on=added), POLICY, NOW) is None


def test_a_torrent_without_readable_tracker_follows_the_general_rule():
    obligation = seed_obligation(torrent(trackers=()), POLICY, NOW)
    assert obligation is not None and obligation.tracker is None


def test_the_most_demanding_tracker_wins():
    policy = SeedPolicy(
        enabled=True,
        private_min_days=14,
        tracker_rules=(TrackerSeedRule(domain="long.example", min_days=60),),
    )

    obligation = seed_obligation(torrent(days_ago=20, trackers=("short.example", "long.example")), policy, NOW)

    assert obligation is not None and (obligation.tracker, obligation.min_days) == ("long.example", 60)


def test_unreadable_rules_are_ignored_and_logged(caplog):
    settings = Settings(id=1, seed_tracker_rules="pas du json")
    with caplog.at_level(logging.WARNING, logger="app.services.seed_protection"):
        assert seed_policy(settings).tracker_rules == ()
    assert "illisibles" in caplog.text


def test_ten_thousand_torrents_are_checked_quickly():
    torrents = [torrent(days_ago=i % 30) for i in range(10_000)]

    started = time.monotonic()
    free, protected = split_protected(torrents, POLICY, NOW)
    elapsed = time.monotonic() - started

    assert len(free) + len(protected) == 10_000 and protected
    assert elapsed < 1.0, f"{elapsed:.2f} s"


# --- Nettoyage et automatisations -----------------------------------------------------


def _protect(session: Session, settings: Settings) -> None:
    settings.seed_protection_enabled = True
    session.add(settings)
    session.commit()


def _orphan(session: Session, media: Media, name: str, days_ago: float) -> Torrent:
    row = Torrent(
        media_id=media.id,
        hash=name,
        name=name,
        size=1000,
        is_hardlinked=False,
        repairable=False,
        is_private=True,
        completed_on=datetime.now(UTC) - timedelta(days=days_ago),
        trackers_json=json.dumps([{"domain": "tracker.example.org", "status": "ok"}]),
    )
    session.add(row)
    session.commit()
    return row


def _media(session: Session) -> Media:
    media = Media(media_type=MediaType.movie, title="Film", statuses="orphelin_qbit")
    session.add(media)
    session.commit()
    return media


def test_the_cleanup_preview_shows_protected_torrents_apart(session, settings):
    _protect(session, settings)
    media = _media(session)
    _orphan(session, media, "recent", days_ago=3)
    _orphan(session, media, "ancien", days_ago=40)

    preview = build_delete_preview(session, media)

    assert [item.label for item in preview.items] == ["ancien"]
    assert preview.total_reclaimable_bytes == 1000
    [protected] = preview.protected
    assert (protected.label, protected.obligation.reason, protected.obligation.tracker) == (
        "recent",
        "min_seed",
        "tracker.example.org",
    )
    assert protected.obligation.until is not None


def test_the_cleanup_never_deletes_a_protected_torrent(session, settings, fake_http):
    _protect(session, settings)
    media = _media(session)
    _orphan(session, media, "recent", days_ago=3)
    calls = []
    fake_http["http://qbit"] = calls.append

    result = asyncio.run(execute_delete(session, media, settings))

    assert result.steps == [] and calls == []
    assert session.get(Torrent, 1) is not None


def test_existing_installs_are_not_protected_by_default(session, settings):
    media = _media(session)
    _orphan(session, media, "recent", days_ago=3)

    assert [item.label for item in build_delete_preview(session, media).items] == ["recent"]


def _cleanup_rule(**fields) -> Automation:
    return Automation(
        name="r",
        trigger="orphan_detected",
        action="cleanup",
        conditions=json.dumps({}),
        max_actions=10,
        **fields,
    )


def test_a_cleanup_rule_skips_media_whose_orphans_are_all_protected(session, settings):
    _protect(session, settings)
    protected_only = _media(session)
    _orphan(session, protected_only, "recent", days_ago=3)
    mixed = Media(media_type=MediaType.movie, title="Mixte", statuses="orphelin_qbit")
    session.add(mixed)
    session.commit()
    _orphan(session, mixed, "recent-2", days_ago=3)
    _orphan(session, mixed, "ancien", days_ago=40)

    eligible = eligible_medias(session, as_rule(_cleanup_rule()))

    assert [(media.title, [t.name for t in torrents]) for media, torrents in eligible] == [("Mixte", ["ancien"])]


def test_a_notification_rule_still_sees_protected_orphans(session, settings):
    _protect(session, settings)
    media = _media(session)
    _orphan(session, media, "recent", days_ago=3)

    rule = _cleanup_rule()
    rule.action = "notify_only"

    assert [m.title for m, _ in eligible_medias(session, as_rule(rule))] == ["Film"]


def test_the_seed_condition_applies_to_free_orphans_only(session, settings):
    _protect(session, settings)
    media = _media(session)
    _orphan(session, media, "recent", days_ago=3)
    _orphan(session, media, "ancien", days_ago=40)

    rule = _cleanup_rule()
    rule.conditions = json.dumps({"min_seed_days": 30})

    assert [m.title for m, _ in eligible_medias(session, as_rule(rule))] == ["Film"]


# --- Installation existante contre nouvelle -------------------------------------------


_SEED_COLUMNS = (
    "seed_protection_enabled",
    "seed_protection_prompt_dismissed",
    "seed_private_min_days",
    "seed_public_enabled",
    "seed_public_min_days",
    "seed_tracker_rules",
)


def test_an_existing_install_gets_the_protection_off_with_a_prompt(session):
    session.add(Settings(id=1, emby_url="http://emby"))
    session.commit()
    session.close()
    with engine.begin() as connection:
        for column in _SEED_COLUMNS:
            connection.execute(text(f"ALTER TABLE settings DROP COLUMN {column}"))

    init_db()

    with Session(engine) as fresh:
        settings = fresh.get(Settings, 1)
        assert settings is not None and settings.emby_url == "http://emby"
        assert (settings.seed_protection_enabled, settings.seed_private_min_days) == (False, 14)
        assert seed_policy(settings).enabled is False


def test_a_new_install_is_protected_without_prompt():
    settings = Settings(id=1)
    assert settings.seed_protection_enabled is True
    assert seed_policy(settings).enabled is True


# --- Routes ---------------------------------------------------------------------------


def test_the_prompt_is_offered_then_dismissed(admin_client, settings):
    assert admin_client.get(ROUTE).json()["prompt"] is True

    response = admin_client.post(f"{ROUTE}/dismiss-prompt")

    assert response.status_code == 200
    assert (response.json()["prompt"], response.json()["enabled"]) == (False, False)


def test_saving_stores_the_rules_and_closes_the_prompt(admin_client, session, settings):
    _orphan(session, _media(session), "t", days_ago=1)
    payload = {
        "enabled": True,
        "private_min_days": 21,
        "public_enabled": True,
        "public_min_days": 2,
        "tracker_rules": [{"domain": " Tracker.Example.ORG. ", "min_days": 30, "min_ratio": 1.5}],
    }

    response = admin_client.put(ROUTE, json=payload)

    body = response.json()
    assert response.status_code == 200
    assert (body["enabled"], body["private_min_days"], body["public_min_days"], body["prompt"]) == (True, 21, 2, False)
    assert body["tracker_rules"] == [{"domain": "tracker.example.org", "min_days": 30, "min_ratio": 1.5}]
    assert body["known_trackers"] == ["tracker.example.org"]
    session.expire_all()
    assert seed_policy(session.get(Settings, 1)).tracker_rules[0].min_ratio == 1.5


BASE = {"enabled": True, "private_min_days": 14, "public_enabled": False, "public_min_days": 3, "tracker_rules": []}


@pytest.mark.parametrize(
    "change",
    [
        {"private_min_days": 366},
        {"private_min_days": -1},
        {"public_min_days": 400},
        {"tracker_rules": [{"domain": "https://tracker.example.org/announce?passkey=x", "min_days": 1}]},
        {"tracker_rules": [{"domain": "tracker", "min_days": 1}]},
        {"tracker_rules": [{"domain": "a.example", "min_days": 1, "min_ratio": 101}]},
        {"tracker_rules": [{"domain": "a.example", "min_days": 1}, {"domain": "A.example", "min_days": 2}]},
        {"tracker_rules": [{"domain": f"t{i}.example", "min_days": 1} for i in range(51)]},
    ],
)
def test_invalid_settings_are_refused(admin_client, change):
    assert admin_client.put(ROUTE, json=BASE | change).status_code == 422


def test_the_routes_require_a_session(client):
    assert client.get(ROUTE).status_code == 401
    assert client.put(ROUTE, json=BASE).status_code == 401
    assert client.post(f"{ROUTE}/dismiss-prompt").status_code == 401


def test_the_detail_shows_the_obligation_of_each_torrent(admin_client, session, settings):
    _protect(session, settings)
    media = _media(session)
    _orphan(session, media, "recent", days_ago=3)

    [row] = admin_client.get(f"/api/media/{media.id}").json()["torrents"]

    assert (row["is_private"], row["seed_obligation"]["reason"]) == (True, "min_seed")
    assert row["seed_obligation"]["tracker"] == "tracker.example.org"
