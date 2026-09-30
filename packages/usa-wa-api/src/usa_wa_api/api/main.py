"""FastAPI application entry point."""

import asyncio
from contextlib import asynccontextmanager

from fastapi import APIRouter, FastAPI
from fastapi.responses import JSONResponse
from sqlalchemy import text

from clearinghouse_core.config import get_settings
from clearinghouse_core.database import get_session_factory, log_connection_fingerprint
from clearinghouse_core.logging import configure_logging, get_logger
from usa_wa_api.api.datasets import router as datasets_router
from usa_wa_api.api.serving import router as serving_router
from usa_wa_api.api.v1 import router as v1_router

logger = get_logger(__name__)

# How long /ready waits for its SELECT 1 before answering 503 (#442).
READY_TIMEOUT_S = 3.0

# Checks /ready stopped waiting for, held until their cleanup ends: the event loop
# keeps only weak references to tasks, and a ROLLBACK on a dead connection can
# take minutes to fail.
_abandoned_checks: set[asyncio.Task[None]] = set()


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


def _not_ready() -> JSONResponse:
    """The 503 body every failed or timed-out readiness check answers with."""
    return JSONResponse(status_code=503, content={"status": "not_ready", "db": False})


@health_router.get("/ready")
async def ready() -> JSONResponse:
    """Readiness probe — checks DB connectivity. Returns 503 on failure.

    Answers within ``READY_TIMEOUT_S`` whatever the database does. The check runs
    in its own task (#442) because a timeout inside it cannot bound it:
    ``AsyncSession`` closes in a shielded task, so a cancelled ``SELECT 1`` on a
    wedged connection hands off to a ROLLBACK on that same connection, unbounded.
    Past the bound the check is abandoned, not awaited.
    """
    check = asyncio.create_task(_select_one())
    try:
        done, _ = await asyncio.wait({check}, timeout=READY_TIMEOUT_S)
    except asyncio.CancelledError:
        _abandon(check)
        raise
    if not done:
        _abandon(check)
        logger.warning("readiness check timed out", extra={"timeout_s": READY_TIMEOUT_S})
        return _not_ready()
    try:
        check.result()
    # Any exception, not SQLAlchemyError (#433): SQLAlchemy's asyncpg dialect
    # wraps no connect-time error, so a stopped server's bare
    # ConnectionRefusedError and asyncpg's "starting up" both escaped as 500s.
    except Exception:  # noqa: BLE001 — every failed SELECT 1 means not ready
        # Catching it drops the cause the 500's traceback put in the journal.
        logger.warning("readiness check failed", exc_info=True)
        return _not_ready()
    return JSONResponse(status_code=200, content={"status": "ready", "db": True})


async def _select_one() -> None:
    """Run ``SELECT 1`` on a fresh session from the shared factory."""
    async with get_session_factory()() as session:
        await session.execute(text("SELECT 1"))


def _abandon(check: asyncio.Task[None]) -> None:
    """Cancel a check /ready no longer waits for, and hold it until it ends."""
    check.cancel()
    _abandoned_checks.add(check)
    check.add_done_callback(_reap)


def _reap(check: asyncio.Task[None]) -> None:
    """Drop a finished abandoned check, logging how it ended if not by the cancel.

    The 503 went out long ago; this is the journal's only word on what became of
    the connection — typically a ROLLBACK on it that finally failed. Retrieving
    the exception also keeps it from being reported as never retrieved.
    """
    _abandoned_checks.discard(check)
    if check.cancelled():
        return
    error = check.exception()
    if error is not None:
        logger.info("abandoned readiness check ended", exc_info=error)


app.include_router(health_router)
# The published-dataset surface (#311): /datasets/* static files + the
# /health/datasets publication probe, which succeeded /health/sync when the
# PM-sync outbox stopped being the thing an operator watches (#313).
app.include_router(datasets_router)
# The API's own projection of that contract (#313): /health/serving answers
# "did this deployment load what was published", which /health/datasets cannot.
app.include_router(serving_router)
# The product surface (#184). Read-only in the literal sense since #313: with
# `POST /sync/redrive` retired this app registers no mutating route at all, which
# is what let Power Map revoke usa-wa's write scopes against an API that
# provably cannot write. Mounted last: the unversioned probes above are
# deployment contracts and keep their paths.
app.include_router(v1_router)
