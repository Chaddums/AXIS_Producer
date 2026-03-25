"""Auth routes — signup, login, email verification, password reset, token refresh."""

import secrets
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel, EmailStr
from slowapi import Limiter
from slowapi.util import get_remote_address

limiter = Limiter(key_func=get_remote_address)

import auth
import db
from prohibited_use import check_email_domain, scan_org_name, log_attestation, flag_account

router = APIRouter(prefix="/auth", tags=["auth"])


class SignupRequest(BaseModel):
    email: EmailStr
    password: str
    name: str
    organization: str = ""
    self_attested: bool = False


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class TokenResponse(BaseModel):
    token: str
    user_id: str
    teams: list[str]
    email_verified: bool = False


class VerifyEmailRequest(BaseModel):
    token: str


class ForgotPasswordRequest(BaseModel):
    email: EmailStr


class ResetPasswordRequest(BaseModel):
    token: str
    new_password: str


class RefreshRequest(BaseModel):
    token: str


@router.post("/signup", response_model=TokenResponse)
@limiter.limit("5/hour")
async def signup(req: SignupRequest, request: Request):
    # Prohibited use: block known government domains
    if check_email_domain(req.email):
        raise HTTPException(status_code=400, detail="Unable to create account")

    # Require self-attestation
    if not req.self_attested:
        raise HTTPException(status_code=400, detail="Self-attestation required")

    if db.get_user_by_email(req.email):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Email already registered")

    password_hash = auth.hash_password(req.password)
    user = db.create_user(req.email, password_hash, req.name)
    if not user:
        raise HTTPException(status_code=500, detail="Failed to create user")

    # Log attestation (legal artifact)
    client_ip = request.client.host if request.client else None
    log_attestation(user["id"], client_ip)

    # Flag if organization name matches keywords
    if req.organization:
        matched = scan_org_name(req.organization)
        if matched:
            flag_account(user["id"], f"Organization keyword match: {req.organization}", matched)

    # Create email verification token
    verify_token = secrets.token_urlsafe(32)
    expires = (datetime.now(timezone.utc) + timedelta(hours=24)).isoformat()
    db.create_email_verification(user["id"], verify_token, expires)

    token = auth.create_token(user["id"], [])
    return TokenResponse(
        token=token,
        user_id=user["id"],
        teams=[],
        email_verified=False,
    )


@router.post("/login", response_model=TokenResponse)
@limiter.limit("10/minute")
async def login(req: LoginRequest, request: Request):
    user = db.get_user_by_email(req.email)
    if not user or not auth.verify_password(req.password, user["password_hash"]):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials")

    teams = db.get_user_teams(user["id"])
    team_ids = [t["team_id"] for t in teams]

    token = auth.create_token(user["id"], team_ids)
    return TokenResponse(
        token=token,
        user_id=user["id"],
        teams=team_ids,
        email_verified=user.get("email_verified", False),
    )


@router.post("/verify-email")
async def verify_email(req: VerifyEmailRequest):
    verification = db.get_email_verification(req.token)
    if not verification:
        raise HTTPException(status_code=400, detail="Invalid or expired verification token")

    expires = datetime.fromisoformat(verification["expires_at"])
    if datetime.now(timezone.utc) > expires:
        raise HTTPException(status_code=400, detail="Verification token expired")

    db.mark_email_verified(verification["user_id"], verification["id"])
    return {"verified": True}


@router.post("/forgot-password")
@limiter.limit("3/hour")
async def forgot_password(req: ForgotPasswordRequest, request: Request):
    # Always return success to prevent email enumeration
    user = db.get_user_by_email(req.email)
    if user:
        reset_token = secrets.token_urlsafe(32)
        expires = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
        db.create_password_reset(user["id"], reset_token, expires)
        # TODO: send email with reset link (Resend / SES)
        # For now, token is logged server-side only

    return {"sent": True}


@router.post("/reset-password")
async def reset_password(req: ResetPasswordRequest):
    reset = db.get_password_reset(req.token)
    if not reset:
        raise HTTPException(status_code=400, detail="Invalid or expired reset token")

    expires = datetime.fromisoformat(reset["expires_at"])
    if datetime.now(timezone.utc) > expires:
        raise HTTPException(status_code=400, detail="Reset token expired")

    new_hash = auth.hash_password(req.new_password)
    db.use_password_reset(reset["id"], reset["user_id"], new_hash)
    return {"reset": True}


@router.post("/refresh", response_model=TokenResponse)
async def refresh_token(req: RefreshRequest):
    """Issue a new token if the current one is still valid."""
    payload = auth.decode_token(req.token)
    user = db.get_user_by_id(payload["sub"])
    if not user:
        raise HTTPException(status_code=401, detail="User not found")

    teams = db.get_user_teams(user["id"])
    team_ids = [t["team_id"] for t in teams]
    new_token = auth.create_token(user["id"], team_ids)

    return TokenResponse(
        token=new_token,
        user_id=user["id"],
        teams=team_ids,
        email_verified=user.get("email_verified", False),
    )
