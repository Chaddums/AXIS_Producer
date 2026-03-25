"""AXIS Backend — FastAPI entry point."""

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

import auth
import db
import routes_auth
import routes_events
import routes_proxy
import routes_teams
from config import Config


@asynccontextmanager
async def lifespan(app: FastAPI):
    config = Config()
    db.init(config.supabase_url, config.supabase_service_key)
    auth.init(config)
    routes_proxy.init(config)
    yield


app = FastAPI(title="AXIS", version="0.1.0", lifespan=lifespan)

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


@app.get("/health")
async def health():
    return {"status": "ok"}
