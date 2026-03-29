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


class EventStatusUpdate(BaseModel):
    status: str  # 'resolved' or 'dismissed'
    who: str


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


@router.patch("/{event_id}")
async def update_event_status(
    event_id: int,
    body: EventStatusUpdate,
    user: dict = Depends(auth.get_current_user),
):
    if body.status not in ("resolved", "dismissed"):
        raise HTTPException(status_code=400, detail="Status must be 'resolved' or 'dismissed'")

    event = db.get_event_by_id(event_id)
    if not event:
        raise HTTPException(status_code=404, detail="Event not found")

    if event["team_id"] not in user.get("teams", []):
        raise HTTPException(status_code=403, detail="Not a member of this team")

    result = db.update_event_status(event_id, body.status, body.who)
    return {"ok": True, "event": result}


@router.delete("/{event_id}")
async def delete_event(event_id: int, user: dict = Depends(auth.get_current_user)):
    event = db.get_event_by_id(event_id)
    if not event:
        raise HTTPException(status_code=404, detail="Event not found")
    if event["team_id"] not in user.get("teams", []):
        raise HTTPException(status_code=403, detail="Not a member of this team")
    db.client().table("events").delete().eq("id", event_id).execute()
    return {"ok": True}


@router.delete("")
async def delete_events_bulk(
    team_id: str = Query(...),
    source: str = Query(None, description="Delete events matching raw->source"),
    user: dict = Depends(auth.get_current_user),
):
    """Bulk delete events by team + source filter."""
    if team_id not in user.get("teams", []):
        raise HTTPException(status_code=403, detail="Not a member of this team")
    if not source:
        raise HTTPException(status_code=400, detail="source filter required for bulk delete")

    sources = [s.strip() for s in source.split(",")]
    total = 0
    for src in sources:
        res = db.client().table("events").delete().eq("team_id", team_id).eq(
            "raw->>source", src).execute()
        total += len(res.data) if res.data else 0
    return {"ok": True, "deleted": total}


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
    order: str = Query("asc", description="Sort order: asc or desc"),
    event_type: str = Query(None, description="Filter by event_type (comma-separated)"),
    user: dict = Depends(auth.get_current_user),
):
    if team_id not in user.get("teams", []):
        raise HTTPException(status_code=403, detail="Not a member of this team")

    q = db.client().table("events").select("*").eq("team_id", team_id)
    if since_id:
        q = q.gt("id", since_id)
    elif since:
        q = q.gt("ts", since)
    if event_type:
        types = [t.strip() for t in event_type.split(",")]
        q = q.in_("event_type", types)
    q = q.order("id", desc=(order == "desc")).limit(limit)

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
