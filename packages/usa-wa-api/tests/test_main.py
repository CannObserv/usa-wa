"""``GET /ready`` answers 503, never 500, whatever way the database is down (#433).

Unit tier except the last test: the session factory is faked, so no database is
needed. A stopped
Postgres is the case the probe exists for. A pooled connection that the server
terminates arrives wrapped as a ``SQLAlchemyError``, but a *new* connection
attempt raises asyncio's bare ``ConnectionRefusedError`` (an ``OSError``), which
escaped the handler as a 500 during the #430 upgrade. SQLAlchemy's asyncpg
dialect translates no connect-time error, so asyncpg's own — "the database
system is starting up", which is neither of those — escapes the same way.

A database that never answers must not hold the probe either (#442): a
saturated pool, a stalled server or a dropped connect would otherwise wait out
SQLAlchemy's or asyncpg's own timeouts (30 s, 60 s, or none at all). The last
test runs that bound against a real pool, since the fakes cannot show what a
cancelled query leaves behind.
"""

import asyncio
import time
from collections.abc import AsyncGenerator

import asyncpg
import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from usa_wa_api.api import main


class _FailingSession:
    """An ``AsyncSession`` stand-in whose ``execute`` raises ``error``."""

    def __init__(self, error: BaseException) -> None:
        self._error = error

    async def __aenter__(self) -> "_FailingSession":
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        return None

    async def execute(self, *args: object, **kwargs: object) -> None:
        raise self._error


class _HangingSession:
    """An ``AsyncSession`` stand-in that never answers ``execute``, ``close`` or both.

    ``__aexit__`` shields its close in a task, as ``AsyncSession.__aexit__`` does,
    so a cancel that arrives mid-``execute`` cannot interrupt the close after it.
    """

    def __init__(self, *, hang_on: str) -> None:
        self._hang_on = hang_on

    async def __aenter__(self) -> "_HangingSession":
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        await asyncio.shield(asyncio.create_task(self._close()))

    async def _close(self) -> None:
        if self._hang_on in ("close", "both"):
            await asyncio.Event().wait()

    async def execute(self, *args: object, **kwargs: object) -> None:
        if self._hang_on in ("execute", "both"):
            await asyncio.Event().wait()


@pytest.fixture
async def bare_client() -> AsyncGenerator[AsyncClient]:
    """An ``AsyncClient`` on the app with no lifespan and no database."""
    transport = ASGITransport(app=main.app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


@pytest.mark.parametrize(
    "error",
    [
        OperationalError("SELECT 1", {}, Exception("terminating connection")),
        ConnectionRefusedError(111, "Connect call failed ('127.0.0.1', 5432)"),
        OSError("network unreachable"),
        asyncpg.exceptions.CannotConnectNowError("the database system is starting up"),
    ],
    ids=["sqlalchemy-error", "connection-refused", "os-error", "starting-up"],
)
async def test_ready_returns_503_when_db_unreachable(bare_client, monkeypatch, error):
    monkeypatch.setattr(main, "get_session_factory", lambda: lambda: _FailingSession(error))

    response = await bare_client.get("/ready")

    assert response.status_code == 503
    assert response.json() == {"status": "not_ready", "db": False}


async def test_ready_logs_why_it_is_not_ready(bare_client, monkeypatch, caplog):
    """The 503 keeps the cause the 500's traceback used to put in the journal."""
    error = ConnectionRefusedError(111, "Connect call failed ('127.0.0.1', 5432)")
    monkeypatch.setattr(main, "get_session_factory", lambda: lambda: _FailingSession(error))

    with caplog.at_level("WARNING", logger=main.__name__):
        await bare_client.get("/ready")

    [record] = [r for r in caplog.records if r.name == main.__name__]
    assert record.levelname == "WARNING"
    assert record.exc_info is not None and record.exc_info[1] is error


@pytest.mark.parametrize("hang_on", ["execute", "close", "both"])
async def test_ready_returns_503_within_its_bound_when_db_never_answers(
    bare_client, monkeypatch, hang_on
):
    """``both`` is a wedged server: the cancelled ``execute`` hands off to a shielded
    close that hangs on the same connection, and the answer must not wait for it."""
    monkeypatch.setattr(main, "READY_TIMEOUT_S", 0.05)
    monkeypatch.setattr(
        main, "get_session_factory", lambda: lambda: _HangingSession(hang_on=hang_on)
    )

    started = time.monotonic()
    # wait_for keeps a regression a failure, not a hung suite.
    response = await asyncio.wait_for(bare_client.get("/ready"), timeout=5)

    assert time.monotonic() - started < 1
    assert response.status_code == 503
    assert response.json() == {"status": "not_ready", "db": False}


@pytest.mark.db  # opens its own engine; the fixture sweep marks it too
async def test_ready_recovers_once_its_bound_cancels_a_live_query(
    bare_client, monkeypatch, test_engine
):
    """A query the bound cancels must not poison the pool it came from.

    One connection, no overflow: if the cancelled connection went back to the
    pool checked out or broken, the second probe would get it and answer 503.
    """
    engine = create_async_engine(
        test_engine.url.render_as_string(hide_password=False), pool_size=1, max_overflow=0
    )
    statements = iter(["SELECT pg_sleep(10)", "SELECT 1"])
    monkeypatch.setattr(main, "text", lambda _sql: text(next(statements)))
    monkeypatch.setattr(main, "get_session_factory", lambda: async_sessionmaker(engine))
    try:
        monkeypatch.setattr(main, "READY_TIMEOUT_S", 0.2)
        stalled = await asyncio.wait_for(bare_client.get("/ready"), timeout=5)
        monkeypatch.setattr(main, "READY_TIMEOUT_S", 5.0)
        recovered = await asyncio.wait_for(bare_client.get("/ready"), timeout=10)

        assert stalled.status_code == 503
        assert recovered.status_code == 200
        assert engine.pool.checkedout() == 0
    finally:
        await engine.dispose()
