"""Protection du seed (voir services/seed_protection.py) : réglages et
bandeau proposé aux installations existantes. Routes synchrones : FastAPI les
exécute hors de la boucle asyncio."""

import json

from fastapi import APIRouter, Depends
from sqlmodel import Session, select

from app.database import get_session
from app.models.media import Torrent
from app.models.settings import Settings
from app.schemas.seed import TRACKER_RULES, SeedProtectionRead, SeedProtectionWrite
from app.services.seed_protection import prompt_pending, tracker_rules

router = APIRouter()


def _known_trackers(session: Session) -> list[str]:
    domains: set[str] = set()
    for trackers_json in session.exec(select(Torrent.trackers_json)).all():
        try:
            entries = json.loads(trackers_json or "[]")
        except ValueError:
            continue
        domains.update(e["domain"] for e in entries if isinstance(e, dict) and isinstance(e.get("domain"), str))
    return sorted(d for d in domains if d)


def _read(session: Session) -> SeedProtectionRead:
    settings = session.get(Settings, 1) or Settings(id=1)
    return SeedProtectionRead(
        enabled=settings.seed_protection_enabled,
        private_min_days=settings.seed_private_min_days,
        public_enabled=settings.seed_public_enabled,
        public_min_days=settings.seed_public_min_days,
        tracker_rules=tracker_rules(settings),
        prompt=prompt_pending(settings),
        known_trackers=_known_trackers(session),
    )


@router.get("", response_model=SeedProtectionRead)
def read_seed_protection(session: Session = Depends(get_session)) -> SeedProtectionRead:
    return _read(session)


@router.put("", response_model=SeedProtectionRead)
def update_seed_protection(payload: SeedProtectionWrite, session: Session = Depends(get_session)) -> SeedProtectionRead:
    settings = session.get(Settings, 1) or Settings(id=1)
    settings.seed_protection_enabled = payload.enabled
    settings.seed_private_min_days = payload.private_min_days
    settings.seed_public_enabled = payload.public_enabled
    settings.seed_public_min_days = payload.public_min_days
    settings.seed_tracker_rules = TRACKER_RULES.dump_json(payload.tracker_rules).decode()
    # Une fois la question tranchée (activée ou désactivée volontairement),
    # le bandeau ne revient plus.
    settings.seed_protection_prompt_dismissed = True
    session.add(settings)
    session.commit()
    return _read(session)


@router.post("/dismiss-prompt", response_model=SeedProtectionRead)
def dismiss_prompt(session: Session = Depends(get_session)) -> SeedProtectionRead:
    """« Non merci » sur le bandeau : la protection reste désactivée."""
    settings = session.get(Settings, 1) or Settings(id=1)
    settings.seed_protection_prompt_dismissed = True
    session.add(settings)
    session.commit()
    return _read(session)
