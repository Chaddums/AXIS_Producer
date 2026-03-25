"""Event routes — team-scoped event push/pull for cloud sync."""

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

import auth
import db

router = APIRouter(prefix="/events", tags=["events"])


class EventIn(BaseModel):
    team_id: str
    session_id: str
    stream: str
    event_type: str
    who: str
    area: str | None = None
    files: list[str] = []
    summary: str = ""
    raw: dict = {}
    project: str | None = None


@router.post("")
async def push_event(event: EventIn, user: dict = Depends(auth.get_current_user)):
    if event.team_id not in user.get("teams", []):
        raise HTTPException(status_code=403, detail="Not a member of this team")

    res = db.client().table("events").insert({
        "team_id": event.team_id,
        "session_id": event.session_id,
        "stream": event.stream,
        "event_type": event.event_type,
        "who": event.who,
        "area": event.area,
        "files": event.files,
        "summary": event.summary,
        "raw": event.raw,
        "project": event.project,
    }).execute()

    return {"ok": True, "id": res.data[0]["id"] if res.data else None}


@router.get("")
async def pull_events(
    team_id: str = Query(...),
    since: str = Query(None, description="ISO timestamp, return events after this"),
    limit: int = Query(100, le=500),
    user: dict = Depends(auth.get_current_user),
):
    if team_id not in user.get("teams", []):
        raise HTTPException(status_code=403, detail="Not a member of this team")

    q = db.client().table("events").select("*").eq("team_id", team_id)
    if since:
        q = q.gt("ts", since)
    q = q.order("ts", desc=False).limit(limit)

    res = q.execute()
    return res.data or []
