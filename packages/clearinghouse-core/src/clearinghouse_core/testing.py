"""Cross-package test utilities.

Helpers tests at every layer import directly (no fixture indirection).
Currently small — grows as more sibling-reusable test infra needs a home.
"""

from __future__ import annotations

import os

from sqlalchemy.engine import make_url


def assert_test_url_safety(test_url: str) -> None:
    """Raise if ``test_url`` could reach production data.

    Defence-in-depth for destructive tests: any test that opens its own engine
    against ``TEST_DATABASE_URL`` (bypassing the savepointed ``db_session``
    fixture) must call this before issuing DDL or DML. Without it, a
    misconfigured env var can land production data under the test's cleanup
    DELETEs.

    Three independent belts:

    1. ``test_url`` must not equal the production ``DATABASE_URL``.
    2. The test database name must end in ``_test`` — catches a typo pointing
       the test DSN at the prod database even when ``DATABASE_URL`` is unset.
    3. The test DSN must not connect as the *same role* the production
       ``DATABASE_URL`` uses. The forbidden role is derived from
       ``DATABASE_URL``'s username rather than hardcoded, so this stays
       jurisdiction-agnostic and self-maintaining for sibling deployments.

    Intentionally callable at module-import time *and* at test-body time so
    callers can re-assert immediately before any destructive operation.
    """
    prod_url = os.environ.get("DATABASE_URL")
    if prod_url and test_url == prod_url:
        raise RuntimeError(
            "TEST_DATABASE_URL must not equal DATABASE_URL. "
            "Destructive tests would otherwise drop or wipe production rows. "
            "Set TEST_DATABASE_URL to a dedicated test database "
            "(database name should include '_test')."
        )

    url = make_url(test_url)
    if not (url.database or "").endswith("_test"):
        raise RuntimeError(
            f"TEST_DATABASE_URL database name {url.database!r} must end in '_test'. "
            "A test DSN pointed at any other database can wipe non-test rows."
        )
    if prod_url:
        prod_role = make_url(prod_url).username
        if prod_role and url.username == prod_role:
            raise RuntimeError(
                f"TEST_DATABASE_URL must not connect as the same role as production "
                f"({prod_role!r}); use a dedicated test role (e.g. one ending '_test_app')."
            )
