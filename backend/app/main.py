"""FastAPI entrypoint for the AI Revenue Recovery backend.

Run (from backend/):  uv run uvicorn app.main:app --reload
Docs:                 http://localhost:8000/docs
"""

from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api import router as api_router
from app.config import get_settings
from app.db import store

settings = get_settings()


@asynccontextmanager
async def lifespan(app: FastAPI):
    # ensure tables exist on startup
    store.init_db(settings.database_url)
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
