"""API proxy routes — Anthropic and Groq, metered per-team."""

from datetime import datetime, timezone

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

import auth
import db
from config import Config

router = APIRouter(prefix="/proxy", tags=["proxy"])
_config: Config | None = None
_http: httpx.AsyncClient | None = None


def init(config: Config):
    global _config, _http
    _config = config
    _http = httpx.AsyncClient(timeout=120.0)


# --- Cost estimation ---

# Sonnet pricing: $3/M input, $15/M output
ANTHROPIC_COST_PER_INPUT_TOKEN = 3.0 / 1_000_000
ANTHROPIC_COST_PER_OUTPUT_TOKEN = 15.0 / 1_000_000

# Groq Whisper: ~$0.11/audio hour, estimated as $0.002/request for tracking
GROQ_COST_PER_REQUEST = 0.002


def _check_budget(team_id: str):
    """Raise 429 if team has exceeded monthly budget."""
    year_month = datetime.now(timezone.utc).strftime("%Y-%m")
    usage = db.get_monthly_usage(team_id, year_month)
    if usage >= _config.monthly_budget_cap:
        raise HTTPException(
            status_code=429,
            detail=f"Monthly API budget exceeded (${usage:.2f} / ${_config.monthly_budget_cap:.2f})"
        )
    return usage


# --- Anthropic proxy ---

class BatchRequest(BaseModel):
    team_id: str
    system: str
    transcript: str
    model: str = "claude-sonnet-4-20250514"
    max_tokens: int = 1024


@router.post("/anthropic/batch")
async def anthropic_batch(req: BatchRequest, user: dict = Depends(auth.get_current_user)):
    if req.team_id not in user.get("teams", []):
        raise HTTPException(status_code=403, detail="Not a member of this team")

    auth.require_active_subscription(req.team_id)
    _check_budget(req.team_id)

    resp = await _http.post(
        "https://api.anthropic.com/v1/messages",
        headers={
            "x-api-key": _config.anthropic_api_key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        },
        json={
            "model": req.model,
            "max_tokens": req.max_tokens,
            "system": req.system,
            "messages": [{"role": "user", "content": req.transcript}],
        },
    )

    if resp.status_code != 200:
        raise HTTPException(status_code=resp.status_code, detail=resp.text)

    data = resp.json()

    # Track usage
    usage = data.get("usage", {})
    tokens_in = usage.get("input_tokens", 0)
    tokens_out = usage.get("output_tokens", 0)
    cost = (tokens_in * ANTHROPIC_COST_PER_INPUT_TOKEN +
            tokens_out * ANTHROPIC_COST_PER_OUTPUT_TOKEN)

    db.record_usage(req.team_id, user["sub"], "anthropic",
                    tokens_in, tokens_out, cost)

    return data


# --- Groq Whisper proxy ---

@router.post("/groq/transcribe")
async def groq_transcribe(request: Request, user: dict = Depends(auth.get_current_user)):
    """Proxy audio to Groq Whisper. Expects multipart form with team_id and audio file."""
    form = await request.form()
    team_id = form.get("team_id")
    audio = form.get("audio")

    if not team_id or not audio:
        raise HTTPException(status_code=400, detail="team_id and audio file required")

    if team_id not in user.get("teams", []):
        raise HTTPException(status_code=403, detail="Not a member of this team")

    auth.require_active_subscription(team_id)
    _check_budget(team_id)

    # Forward to Groq
    audio_bytes = await audio.read()
    resp = await _http.post(
        "https://api.groq.com/openai/v1/audio/transcriptions",
        headers={"Authorization": f"Bearer {_config.groq_api_key}"},
        files={"file": (audio.filename or "audio.wav", audio_bytes, audio.content_type or "audio/wav")},
        data={"model": "whisper-large-v3", "response_format": "json"},
    )

    if resp.status_code != 200:
        raise HTTPException(status_code=resp.status_code, detail=resp.text)

    data = resp.json()

    db.record_usage(team_id, user["sub"], "groq", 0, 0, GROQ_COST_PER_REQUEST)

    return data
