"""Résumé hebdomadaire (roadmap Phase 5) : croissance, remplissage estimé,
meilleurs candidats, fins d'obligation de seed ; opt-in par abonnement,
jamais calculé sans abonné, et envoi à la demande."""

import asyncio
import json
from datetime import UTC, datetime, timedelta

import httpx
from apscheduler.triggers.cron import CronTrigger
from sqlmodel import Session

from app.models.library_snapshot import LibrarySnapshot
from app.models.media import Media, MediaType, ScanRun, ScanStatus, Torrent
from app.models.notification_channel import NotificationChannel
from app.services import weekly_summary
from app.services.library_history import today
from app.services.notifications import NOTIFICATION_EVENTS, weekly_summary_notification
from app.services.scheduler import configure_weekly_summary, scheduler
from app.services.weekly_summary import build_weekly_summary, send_weekly_summary

GB = 1024**3
WEBHOOK = "https://discord.com/api/webhooks/1/secret"


def _known_library(session: Session) -> None:
    session.add(ScanRun(status=ScanStatus.completed, scope="full"))
    session.commit()


def _media(session: Session, title: str, size: int, added_days_ago: int = 400) -> Media:
    media = Media(
        media_type=MediaType.movie,
        title=title,
        year=2001,
        total_size=size,
        full_reclaimable_bytes=size,
        emby_date_added=datetime.now(UTC) - timedelta(days=added_days_ago),
    )
    session.add(media)
    session.commit()
    session.refresh(media)
    return media


def _snapshots(session: Session, days: int, used_per_day: int = 5 * GB) -> None:
    for i in range(days):
        used = 500 * GB + used_per_day * i
        disk = {"role": "library", "path": "/media", "available": True, "total": 1000 * GB, "free": 1000 * GB - used,
                "device": 1}  # fmt: skip
        day = today() - timedelta(days=days - 1 - i)
        session.add(LibrarySnapshot(day=day, total_size=100 * GB + GB * i, disks=json.dumps([disk])))
    session.commit()


def _subscribe(session: Session, events: list[str]) -> None:
    session.add(NotificationChannel(kind="discord", name="Discord", url=WEBHOOK, events=json.dumps(events)))
    session.commit()


def test_the_event_is_notifiable():
    assert "weekly_summary" in NOTIFICATION_EVENTS


def test_nothing_to_summarize_before_a_full_scan(session, settings):
    assert build_weekly_summary(session) is None


def test_the_summary_gathers_growth_disks_and_candidates(session, settings):
    _known_library(session)
    _snapshots(session, 20)
    for i in range(12):
        _media(session, f"Film {i:02d}", size=(i + 1) * GB)

    summary = build_weekly_summary(session)

    assert summary is not None
    assert summary.media_count == 12 and summary.library_size == 78 * GB
    # Photographie d'il y a 7 jours : 100 + 12 Go.
    assert summary.week_growth == 78 * GB - 112 * GB
    assert summary.candidate_count == 12 and len(summary.top_candidates) == 10
    [disk] = summary.disks
    assert disk.fill is not None
    assert summary.seed_releases is None  # protection du seed désactivée


def test_torrents_leaving_their_seed_obligation_within_a_week(session, settings):
    settings.seed_protection_enabled = True
    settings.seed_private_min_days = 14
    session.add(settings)
    _known_library(session)
    media = _media(session, "Film", 10 * GB)
    now = datetime.now(UTC)
    for name, days in (("Bientôt libre", 10), ("Encore longtemps", 1), ("Déjà libre", 30)):
        completed = now - timedelta(days=days)
        session.add(Torrent(media_id=media.id, hash=name, name=name, is_private=True, completed_on=completed))
    session.commit()

    summary = build_weekly_summary(session, now)

    assert summary is not None and summary.seed_releases is not None
    assert [r.name for r in summary.seed_releases] == ["Bientôt libre"]


def test_the_message_reads_well_in_both_languages(session, settings):
    _known_library(session)
    _snapshots(session, 20)
    _media(session, "Le Film", 42 * GB)
    summary = build_weekly_summary(session)
    assert summary is not None

    fr = weekly_summary_notification("fr", summary)
    fields = dict(fr.fields)
    assert fr.title == "Résumé de la semaine"
    assert fields["Bibliothèque"] == "1 média · 42.0 Go"
    # 405 Go libres, 5 Go par jour sans incertitude : 81 jours.
    assert fields["Disque Bibliothèque"] == "60 % occupé — plein dans environ 12 semaines"
    assert fr.details == ["Le Film (2001) — 42.0 Go · score " + str(summary.top_candidates[0].score)]

    en = weekly_summary_notification("en", summary)
    assert en.title == "Weekly summary" and dict(en.fields)["Disk Library"] == "60% used — full in about 12 weeks"


def test_nothing_is_computed_without_a_subscriber(session, settings, monkeypatch, fake_http):
    _known_library(session)
    _subscribe(session, ["scan_completed"])
    monkeypatch.setattr(weekly_summary, "build_weekly_summary", lambda *a: (_ for _ in ()).throw(AssertionError))

    delivery = asyncio.run(send_weekly_summary())

    assert delivery.skipped == "no_subscriber" and delivery.results == {}


def test_the_summary_reaches_subscribed_channels_only(session, settings, fake_http):
    _known_library(session)
    _subscribe(session, ["weekly_summary"])
    received: list[httpx.Request] = []
    fake_http["https://discord.com"] = lambda req: received.append(req) or httpx.Response(204)

    delivery = asyncio.run(send_weekly_summary())

    assert delivery.results == {"Discord": None} and len(received) == 1
    assert "Résumé de la semaine" in received[0].content.decode()


def test_send_now_route(admin_client, client, session, settings, fake_http):
    _subscribe(session, ["weekly_summary"])
    body = admin_client.post("/api/notifications/weekly-summary").json()
    assert body == {"channels": [], "skipped": "library_unknown"}


def test_send_now_requires_a_session(client):
    assert client.post("/api/notifications/weekly-summary").status_code == 401


def test_ranges_share_their_unit_like_the_interface():
    from app.services.notifications import _fill_range

    assert _fill_range("fr", 42, 63) == "plein dans 6 à 9 semaines"
    assert _fill_range("en", 10, 300) == "full in 10 days to 10 months"
    assert _fill_range("fr", 1, 1) == "plein dans environ 1 jour"


def test_the_job_runs_on_monday_morning():
    configure_weekly_summary()
    job = scheduler.get_job("weekly_summary")
    assert job is not None and isinstance(job.trigger, CronTrigger)
    fields = {f.name: str(f) for f in job.trigger.fields}
    assert (fields["day_of_week"], fields["hour"]) == ("mon", "9")
    scheduler.remove_job("weekly_summary")
