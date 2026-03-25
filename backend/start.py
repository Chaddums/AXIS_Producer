"""Startup script — reads PORT from env, runs uvicorn."""
import os
import uvicorn

port = int(os.environ.get("PORT", 8000))
uvicorn.run("main:app", host="0.0.0.0", port=port)
