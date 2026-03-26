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
    parent_id: int | None = None


class SynthesisIn(BaseModel):
    team_id: str
    content: str
    window_start: str
    window_end: str


@router.post("")
async def push_event(event: EventIn, user: dict = Depends(auth.get_current_user)):
    if event.team_id not in user.get("teams", []):
        raise HTTPException(status_code=403, detail="Not a member of this team")

    row = {
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
    }
    if event.parent_id:
        row["parent_id"] = event.parent_id

    res = db.client().table("events").insert(row).execute()

    return {"ok": True, "id": res.data[0]["id"] if res.data else None}


@router.post("/batch")
async def push_events_batch(
    events: list[EventIn],
    user: dict = Depends(auth.get_current_user),
):
    """Insert multiple events at once."""
    if not events:
        return {"ok": True, "count": 0}

    # Verify team membership for all events
    team_ids = {e.team_id for e in events}
    user_teams = set(user.get("teams", []))
    if not team_ids.issubset(user_teams):
        raise HTTPException(status_code=403, detail="Not a member of one or more teams")

    rows = []
    for e in events:
        row = {
            "team_id": e.team_id,
            "session_id": e.session_id,
            "stream": e.stream,
            "event_type": e.event_type,
            "who": e.who,
            "area": e.area,
            "files": e.files,
            "summary": e.summary,
            "raw": e.raw,
            "project": e.project,
        }
        if e.parent_id:
            row["parent_id"] = e.parent_id
        rows.append(row)

    res = db.client().table("events").insert(rows).execute()
    return {"ok": True, "count": len(res.data) if res.data else 0}


@router.get("")
async def pull_events(
    team_id: str = Query(...),
    since: str = Query(None, description="ISO timestamp, return events after this"),
    since_id: str = Query(None, description="Return events with id > this value"),
    limit: int = Query(100, le=500),
    user: dict = Depends(auth.get_current_user),
):
    if team_id not in user.get("teams", []):
        raise HTTPException(status_code=403, detail="Not a member of this team")

    q = db.client().table("events").select("*").eq("team_id", team_id)
    if since_id:
        q = q.gt("id", since_id)
    elif since:
        q = q.gt("ts", since)
    q = q.order("ts", desc=False).limit(limit)

    res = q.execute()
    return res.data or []


@router.post("/synthesis")
async def push_synthesis(syn: SynthesisIn, user: dict = Depends(auth.get_current_user)):
    if syn.team_id not in user.get("teams", []):
        raise HTTPException(status_code=403, detail="Not a member of this team")

    result = db.insert_synthesis(
        syn.team_id, syn.content, syn.window_start, syn.window_end
    )
    return {"ok": True, "id": result["id"] if result else None}


@router.get("/synthesis/latest")
async def get_latest_synthesis(
    team_id: str = Query(...),
    user: dict = Depends(auth.get_current_user),
):
    if team_id not in user.get("teams", []):
        raise HTTPException(status_code=403, detail="Not a member of this team")

    result = db.get_latest_synthesis(team_id)
    if not result:
        return {"synthesis": None}
    return result
