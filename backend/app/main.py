"""FastAPI entrypoint for the AI Revenue Recovery backend.

Run (from backend/):  uv run uvicorn app.main:app --reload
Docs:                 http://localhost:8000/docs
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api import router as api_router
from app.api.payment_routes import router as payment_router
from app.config import get_settings
from app.db import store
from app.webhooks import router as webhooks_router

settings = get_settings()
logger = logging.getLogger("app.main")


@asynccontextmanager
async def lifespan(app: FastAPI):
    # ensure tables exist on startup
    store.init_db(settings.database_url)
    # Surface config-that-looks-silent at a glance -- Sarvam degrading to the
    # browser voice is otherwise indistinguishable from "not configured" vs
    # "a stale process never picked up the .env edit" without a debugging pass.
    logger.info(
        "Sarvam TTS: %s",
        "configured" if settings.sarvam_api_key else "not configured (browser voice fallback)",
    )
    yield


app = FastAPI(title="AI Revenue Recovery", version="0.1.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[settings.frontend_origin],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


app.include_router(api_router)
app.include_router(webhooks_router)
app.include_router(payment_router)
