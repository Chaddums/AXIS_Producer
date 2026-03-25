"""Team routes — create, invite, join, list members."""

import secrets

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel

import auth
import db

router = APIRouter(prefix="/teams", tags=["teams"])


class CreateTeamRequest(BaseModel):
    name: str


class TeamResponse(BaseModel):
    id: str
    name: str
    owner_id: str


class InviteResponse(BaseModel):
    code: str
    team_id: str


class JoinRequest(BaseModel):
    code: str


@router.post("", response_model=TeamResponse)
async def create_team(req: CreateTeamRequest, user: dict = Depends(auth.get_current_user)):
    team = db.create_team(req.name, user["sub"])
    if not team:
        raise HTTPException(status_code=500, detail="Failed to create team")

    db.add_team_member(team["id"], user["sub"], role="owner")
    return TeamResponse(id=team["id"], name=team["name"], owner_id=team["owner_id"])


@router.get("", response_model=list[TeamResponse])
async def list_teams(user: dict = Depends(auth.get_current_user)):
    memberships = db.get_user_teams(user["sub"])
    return [
        TeamResponse(
            id=m["teams"]["id"],
            name=m["teams"]["name"],
            owner_id=m["teams"]["owner_id"],
        )
        for m in memberships if m.get("teams")
    ]


@router.post("/{team_id}/invite", response_model=InviteResponse)
async def create_invite(team_id: str, user: dict = Depends(auth.get_current_user)):
    if team_id not in user.get("teams", []):
        raise HTTPException(status_code=403, detail="Not a member of this team")

    code = secrets.token_urlsafe(16)
    invite = db.create_invite(team_id, code, user["sub"])
    if not invite:
        raise HTTPException(status_code=500, detail="Failed to create invite")

    return InviteResponse(code=code, team_id=team_id)


@router.post("/join")
async def join_team(req: JoinRequest, user: dict = Depends(auth.get_current_user)):
    invite = db.get_invite(req.code)
    if not invite:
        raise HTTPException(status_code=404, detail="Invalid or expired invite code")

    db.add_team_member(invite["team_id"], user["sub"], role="member")
    db.mark_invite_used(invite["id"], user["sub"])

    team = db.get_team(invite["team_id"])
    return {"team_id": invite["team_id"], "team_name": team["name"] if team else ""}


@router.get("/{team_id}/members")
async def list_members(team_id: str, user: dict = Depends(auth.get_current_user)):
    if team_id not in user.get("teams", []):
        raise HTTPException(status_code=403, detail="Not a member of this team")

    return db.get_team_members(team_id)
