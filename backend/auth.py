"""Authentication — JWT tokens, password hashing, request dependencies."""

from datetime import datetime, timedelta, timezone

import bcrypt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError, jwt

from config import Config

_bearer = HTTPBearer()
_config: Config | None = None


def init(config: Config):
    global _config
    _config = config


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()


def verify_password(plain: str, hashed: str) -> bool:
    return bcrypt.checkpw(plain.encode(), hashed.encode())


def create_token(user_id: str, team_ids: list[str] | None = None) -> str:
    expire = datetime.now(timezone.utc) + timedelta(hours=_config.jwt_expire_hours)
    payload = {
        "sub": user_id,
        "teams": team_ids or [],
        "exp": expire,
    }
    return jwt.encode(payload, _config.jwt_secret, algorithm=_config.jwt_algorithm)


def decode_token(token: str) -> dict:
    try:
        return jwt.decode(token, _config.jwt_secret, algorithms=[_config.jwt_algorithm])
    except JWTError:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token")


async def get_current_user(
    creds: HTTPAuthorizationCredentials = Depends(_bearer),
) -> dict:
    """FastAPI dependency — extracts and validates the JWT, returns payload."""
    return decode_token(creds.credentials)


async def require_team(team_id: str, user: dict = Depends(get_current_user)) -> dict:
    """Verify the user belongs to the requested team."""
    if team_id not in user.get("teams", []):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not a member of this team")
    return user
