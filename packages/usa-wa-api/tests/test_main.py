"""``GET /ready`` answers 503, never 500, whatever way the database is down (#433).

Unit tier except the last two tests: the session factory is faked, so no
database is needed. A stopped Postgres is the case the probe exists for. A
pooled connection that the server terminates arrives wrapped as a
``SQLAlchemyError``, but a *new* connection attempt raises asyncio's bare
``ConnectionRefusedError`` (an ``OSError``), which escaped the handler as a 500
during the #430 upgrade. SQLAlchemy's asyncpg dialect translates no connect-time
error, so asyncpg's own — "the database system is starting up", which is
neither of those — escapes the same way.

A database that never answers must not hold the probe either (#442): a
saturated pool, a stalled server or a dropped connect would otherwise wait out
SQLAlchemy's or asyncpg's own timeouts (30 s, 60 s, or none at all). The last
two tests run that bound against real connections, since a fake cannot show
what a cancelled query leaves behind — the fakes once passed while a real
black-holed connection held the probe well past it.
"""

import asyncio
import time
from collections.abc import AsyncGenerator

import asyncpg
import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.engine import make_url
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
    """An ``AsyncSession`` stand-in whose ``execute``, ``close`` or both hang until
    ``release`` is set.

    ``__aexit__`` shields its close in a task, as ``AsyncSession.__aexit__`` does,
    so a cancel that arrives mid-``execute`` cannot interrupt the close after it.
    Once released, the close raises ``close_error`` if one is given: the ROLLBACK
    on a dead connection that finally fails.
    """

    def __init__(
        self,
        *,
        hang_on: str,
        release: asyncio.Event,
        close_error: BaseException | None = None,
    ) -> None:
        self._hang_on = hang_on
        self._release = release
        self._close_error = close_error

    async def __aenter__(self) -> "_HangingSession":
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        await asyncio.shield(asyncio.create_task(self._close()))

    async def _close(self) -> None:
        if self._hang_on in ("close", "both"):
            await self._release.wait()
        if self._close_error is not None:
            raise self._close_error

    async def execute(self, *args: object, **kwargs: object) -> None:
        if self._hang_on in ("execute", "both"):
            await self._release.wait()


class _BlackHoleProxy:
    """A TCP proxy to Postgres that, once ``drop()`` is called, swallows traffic.

    No reply and no RST: the firewall-drop or vanished-host shape, where a client
    learns nothing until its own timeout. ``close()`` shuts every socket, which is
    what finally fails a client still waiting on one.
    """

    def __init__(self, host: str, port: int) -> None:
        self._upstream = (host, port)
        self._dropping = asyncio.Event()
        self._tasks: set[asyncio.Task[None]] = set()
        self._writers: list[asyncio.StreamWriter] = []
        self._server: asyncio.Server | None = None

    async def start(self) -> int:
        """Listen on an ephemeral loopback port and return it."""
        self._server = await asyncio.start_server(self._accept, "127.0.0.1", 0)
        return self._server.sockets[0].getsockname()[1]

    def drop(self) -> None:
        """Stop forwarding in both directions, silently."""
        self._dropping.set()

    async def close(self) -> None:
        """Close the listener and every proxied socket, and reap the pipes."""
        assert self._server is not None
        self._server.close()
        for writer in self._writers:
            writer.close()
        for task in self._tasks:
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        await self._server.wait_closed()

    async def _accept(self, client_r: asyncio.StreamReader, client_w: asyncio.StreamWriter) -> None:
        upstream_r, upstream_w = await asyncio.open_connection(*self._upstream)
        self._writers += [client_w, upstream_w]
        for reader, writer in ((client_r, upstream_w), (upstream_r, client_w)):
            task = asyncio.create_task(self._pipe(reader, writer))
            self._tasks.add(task)

    async def _pipe(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        while data := await reader.read(65536):
            if self._dropping.is_set():
                await asyncio.Event().wait()
            writer.write(data)
            await writer.drain()


async def _assert_abandoned_checks_drain() -> None:
    """Every check ``/ready`` gave up on finishes once its connection frees up."""
    if main._abandoned_checks:
        await asyncio.wait(set(main._abandoned_checks), timeout=5)
    assert not main._abandoned_checks


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
    bare_client, monkeypatch, caplog, hang_on
):
    """``both`` is a wedged server: the cancelled ``execute`` hands off to a shielded
    close that hangs on the same connection, and the answer must not wait for it."""
    release = asyncio.Event()
    monkeypatch.setattr(main, "READY_TIMEOUT_S", 0.05)
    monkeypatch.setattr(
        main,
        "get_session_factory",
        lambda: lambda: _HangingSession(hang_on=hang_on, release=release),
    )

    started = time.monotonic()
    try:
        with caplog.at_level("WARNING", logger=main.__name__):
            # wait_for keeps a regression a failure, not a hung suite.
            response = await asyncio.wait_for(bare_client.get("/ready"), timeout=5)
    finally:
        release.set()

    assert time.monotonic() - started < 1
    assert response.status_code == 503
    assert response.json() == {"status": "not_ready", "db": False}
    # Its own message, so the journal tells a timeout from a refused connection.
    [record] = [r for r in caplog.records if r.name == main.__name__]
    assert record.levelname == "WARNING"
    assert record.getMessage() == "readiness check timed out"
    assert record.timeout_s == 0.05
    assert record.exc_info is None
    await _assert_abandoned_checks_drain()


async def test_ready_abandons_its_check_when_the_request_is_cancelled(monkeypatch):
    """A client that hangs up mid-probe cancels ``ready()``; its check must not be
    left running untracked."""
    release = asyncio.Event()
    monkeypatch.setattr(main, "READY_TIMEOUT_S", 5.0)
    monkeypatch.setattr(
        main,
        "get_session_factory",
        lambda: lambda: _HangingSession(hang_on="both", release=release),
    )

    probe = asyncio.create_task(main.ready())
    try:
        await asyncio.sleep(0.05)  # let the check start and hang
        probe.cancel()

        with pytest.raises(asyncio.CancelledError):
            await probe
        assert len(main._abandoned_checks) == 1
    finally:
        release.set()  # a regression fails here, not hangs the loop's teardown
    await _assert_abandoned_checks_drain()


async def test_an_abandoned_check_that_fails_later_says_how(bare_client, monkeypatch, caplog):
    """The 503 has long gone out; the journal still learns how the connection died."""
    release = asyncio.Event()
    error = ConnectionResetError(104, "Connection reset by peer")
    monkeypatch.setattr(main, "READY_TIMEOUT_S", 0.05)
    monkeypatch.setattr(
        main,
        "get_session_factory",
        lambda: lambda: _HangingSession(hang_on="both", release=release, close_error=error),
    )

    with caplog.at_level("INFO", logger=main.__name__):
        try:
            response = await asyncio.wait_for(bare_client.get("/ready"), timeout=5)
        finally:
            release.set()
        await _assert_abandoned_checks_drain()

    assert response.status_code == 503
    [record] = [r for r in caplog.records if r.name == main.__name__ and r.levelname == "INFO"]
    assert record.getMessage() == "abandoned readiness check ended"
    assert record.exc_info is not None and record.exc_info[1] is error


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


@pytest.mark.db  # opens its own engine; the fixture sweep marks it too
async def test_ready_answers_within_its_bound_when_the_db_drops_packets(
    bare_client, monkeypatch, test_engine
):
    """A pooled connection whose packets vanish mid-probe: the real wedged server.

    SQLAlchemy's ``AsyncSession`` closes in a shielded task, so the cancelled
    ``SELECT 1`` hands off to a ROLLBACK on the same dead connection. The answer
    must not wait for it. Closing the proxy then kills that connection, and the
    abandoned check must finish rather than linger on the loop.
    """
    url = test_engine.url
    proxy = _BlackHoleProxy(url.host or "127.0.0.1", url.port or 5432)
    port = await proxy.start()
    engine = create_async_engine(
        make_url(url.render_as_string(hide_password=False)).set(host="127.0.0.1", port=port),
        pool_size=1,
        max_overflow=0,
    )
    monkeypatch.setattr(main, "get_session_factory", lambda: async_sessionmaker(engine))
    try:
        async with async_sessionmaker(engine)() as session:
            await session.execute(text("SELECT 1"))  # pool the connection first
        proxy.drop()
        monkeypatch.setattr(main, "READY_TIMEOUT_S", 0.2)

        started = time.monotonic()
        response = await asyncio.wait_for(bare_client.get("/ready"), timeout=5)

        assert time.monotonic() - started < 2
        assert response.status_code == 503
    finally:
        await proxy.close()
        await engine.dispose()
    await _assert_abandoned_checks_drain()
