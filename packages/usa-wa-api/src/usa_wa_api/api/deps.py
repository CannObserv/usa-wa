"""FastAPI dependencies (database session, auth, etc.)."""

import hmac
import os
from collections.abc import AsyncGenerator

from fastapi import Header, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from clearinghouse_core.database import get_session_factory

#: Env var holding the shared secret that gates mutating operator endpoints.
#: Fail-closed: when unset, no token can match, so the endpoint stays locked.
OPERATOR_TOKEN_ENV = "USA_WA_OPERATOR_TOKEN"


async def get_db_session() -> AsyncGenerator[AsyncSession]:
    """Yield an async DB session, closing it after the request completes."""
    factory = get_session_factory()
    async with factory() as session:
        yield session


async def require_operator(x_operator_token: str | None = Header(default=None)) -> None:
    """Gate a mutating endpoint on the ``X-Operator-Token`` header.

    The header must equal the shared secret in ``USA_WA_OPERATOR_TOKEN``. This is
    the lightest protection consistent with the current API (which has no auth
    framework yet) — a single operator secret, compared in constant time. It is
    fail-closed: an unset env var leaves the endpoint locked for everyone, so a
    misconfigured deployment never silently exposes a state-mutating route.
    """
    expected = os.environ.get(OPERATOR_TOKEN_ENV)
    supplied = x_operator_token or ""
    # Compare on bytes: ``hmac.compare_digest`` raises TypeError on str args that
    # are not ASCII, which would surface as a 500 instead of a clean 401.
    if not expected or not hmac.compare_digest(supplied.encode("utf-8"), expected.encode("utf-8")):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing operator token.",
            headers={"WWW-Authenticate": "X-Operator-Token"},
        )
