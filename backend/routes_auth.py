"""Auth routes — signup, login, token refresh."""

import secrets
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, EmailStr

import auth
import db

router = APIRouter(prefix="/auth", tags=["auth"])


class SignupRequest(BaseModel):
    email: EmailStr
    password: str
    name: str


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class TokenResponse(BaseModel):
    token: str
    user_id: str
    teams: list[str]


@router.post("/signup", response_model=TokenResponse)
async def signup(req: SignupRequest):
    if db.get_user_by_email(req.email):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Email already registered")

    password_hash = auth.hash_password(req.password)
    user = db.create_user(req.email, password_hash, req.name)
    if not user:
        raise HTTPException(status_code=500, detail="Failed to create user")

    token = auth.create_token(user["id"], [])
    return TokenResponse(token=token, user_id=user["id"], teams=[])


@router.post("/login", response_model=TokenResponse)
async def login(req: LoginRequest):
    user = db.get_user_by_email(req.email)
    if not user or not auth.verify_password(req.password, user["password_hash"]):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials")

    teams = db.get_user_teams(user["id"])
    team_ids = [t["team_id"] for t in teams]

    token = auth.create_token(user["id"], team_ids)
    return TokenResponse(token=token, user_id=user["id"], teams=team_ids)
