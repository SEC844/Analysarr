import asyncio
import json
from typing import Optional

from fastapi import APIRouter
from fastapi.responses import StreamingResponse
from sqlmodel import Session, select

from app.database import engine
from app.models.media import ScanRun
from app.schemas.media import ScanRunRead
from app.services.events import scan_events
from app.services.scan import is_scan_running, run_scan

router = APIRouter()


def _to_read(run: ScanRun) -> ScanRunRead:
    return ScanRunRead(
        id=run.id,
        started_at=run.started_at,
        finished_at=run.finished_at,
        status=run.status.value,
        error_message=run.error_message,
        media_count=run.media_count,
        duplicate_count=run.duplicate_count,
        orphan_count=run.orphan_count,
        tracker_unique_count=run.tracker_unique_count,
    )


@router.post("", status_code=202)
async def start_scan() -> dict:
    if is_scan_running():
        return {"started": False, "message": "Un scan est déjà en cours."}
    asyncio.create_task(run_scan())
    return {"started": True}


@router.get("/status", response_model=Optional[ScanRunRead])
def scan_status() -> Optional[ScanRunRead]:
    with Session(engine) as session:
        run = session.exec(select(ScanRun).order_by(ScanRun.started_at.desc())).first()
        return _to_read(run) if run else None


@router.get("/stream")
async def scan_stream() -> StreamingResponse:
    queue = scan_events.subscribe()

    async def gen():
        try:
            while True:
                event = await queue.get()
                yield f"data: {json.dumps(event)}\n\n"
                if event.get("type") in ("completed", "failed"):
                    break
        finally:
            scan_events.unsubscribe(queue)

    return StreamingResponse(gen(), media_type="text/event-stream")
