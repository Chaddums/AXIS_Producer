"""AXIS Backend — FastAPI entry point."""

from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from slowapi import Limiter
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded

import auth
import db
import routes_auth
import routes_billing
import routes_events
import routes_proxy
import routes_teams
from config import Config

limiter = Limiter(key_func=get_remote_address)


@asynccontextmanager
async def lifespan(app: FastAPI):
    config = Config()
    db.init(config.supabase_url, config.supabase_service_key)
    auth.init(config)
    routes_proxy.init(config)
    routes_billing.init(config)
    yield


app = FastAPI(title="AXIS", version="0.2.0", lifespan=lifespan)
app.state.limiter = limiter


@app.exception_handler(RateLimitExceeded)
async def rate_limit_handler(request: Request, exc: RateLimitExceeded):
    return JSONResponse(
        status_code=429,
        content={"detail": "Rate limit exceeded. Try again later."},
    )


app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # tighten before launch
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(routes_auth.router)
app.include_router(routes_teams.router)
app.include_router(routes_proxy.router)
app.include_router(routes_events.router)
app.include_router(routes_billing.router)


@app.get("/health")
async def health():
    return {"status": "ok"}
