"""``GET /ready`` answers 503, never 500, whatever way the database is down (#433).

Unit tier: the session factory is faked, so no database is needed. A stopped
Postgres is the case the probe exists for. A pooled connection that the server
terminates arrives wrapped as a ``SQLAlchemyError``, but a *new* connection
attempt raises asyncio's bare ``ConnectionRefusedError`` (an ``OSError``), which
escaped the handler as a 500 during the #430 upgrade.
"""

from collections.abc import AsyncGenerator

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.exc import OperationalError

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
    ],
    ids=["sqlalchemy-error", "connection-refused", "os-error"],
)
async def test_ready_returns_503_when_db_unreachable(bare_client, monkeypatch, error):
    monkeypatch.setattr(main, "get_session_factory", lambda: lambda: _FailingSession(error))

    response = await bare_client.get("/ready")

    assert response.status_code == 503
    assert response.json() == {"status": "not_ready", "db": False}
