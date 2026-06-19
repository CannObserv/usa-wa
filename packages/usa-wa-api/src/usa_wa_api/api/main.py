"""FastAPI application entry point."""

from contextlib import asynccontextmanager
from dataclasses import asdict
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, FastAPI
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from clearinghouse_core.config import get_settings
from clearinghouse_core.database import get_session_factory, log_connection_fingerprint
from clearinghouse_core.logging import configure_logging, get_logger
from clearinghouse_sync_powermap.engine import outbox_backlog
from usa_wa_api.api.deps import get_db_session
from usa_wa_api.api.redrive import router as redrive_router

logger = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """One-time setup on startup, teardown on shutdown."""
    configure_logging()
    logger.info("application starting")
    session_factory = get_session_factory()
    async with session_factory() as session:
        await log_connection_fingerprint(session, context="api")
    yield
    logger.info("application stopping")


app = FastAPI(title="usa-wa", version="0.1.0", lifespan=lifespan)

health_router = APIRouter(tags=["health"])


@health_router.get("/health")
async def health() -> dict:
    """Liveness probe — confirms the app process is running. No external calls."""
    return {"status": "ok", "build": get_settings().build_id}


@health_router.get("/ready")
async def ready() -> JSONResponse:
    """Readiness probe — checks DB connectivity. Returns 503 on failure."""
    session_factory = get_session_factory()
    async with session_factory() as session:
        try:
            await session.execute(text("SELECT 1"))
            return JSONResponse(status_code=200, content={"status": "ready", "db": True})
        except SQLAlchemyError:
            return JSONResponse(status_code=503, content={"status": "not_ready", "db": False})


@health_router.get("/health/sync")
async def health_sync(session: AsyncSession = Depends(get_db_session)) -> dict:
    """PM-sync outbox backlog — terminal piles (rejected/unavailable) + overdue
    PENDING work, so a stuck or dead-lettered entry is visible. Unauthenticated,
    alongside ``/health``."""
    backlog = await outbox_backlog(session, now=datetime.now(UTC))
    return asdict(backlog)


app.include_router(health_router)
app.include_router(redrive_router)
