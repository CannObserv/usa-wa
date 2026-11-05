"""The `serving` schema stays invisible to alembic (#573).

`serving` carries its own ``MetaData`` so alembic neither owns nor migrates it (see
the :mod:`usa_wa_api.serving.schema` docstring). That holds only if ``alembic/env.py``
also declines to *reflect* it: with ``include_schemas=True`` and no filter, autogenerate
finds the loader's tables, matches none of them in ``Base.metadata``, and proposes
dropping every one — so ``alembic check`` is never clean and ``revision
--autogenerate`` writes a migration that takes ``/api/v1`` down.
"""

import asyncio
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from alembic.autogenerate import produce_migrations
from alembic.config import Config
from alembic.operations.ops import DropTableOp
from alembic.runtime.environment import EnvironmentContext
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

import clearinghouse_domain_legislative  # noqa: F401  (registers the domain's tables)
from clearinghouse_core.models import Base
from usa_wa_api.serving.schema import SCHEMA, ServingBase, include_name

#: The workspace root, where ``alembic/env.py`` lives — four levels above this file.
REPO_ROOT = Path(__file__).resolve().parents[3]


@pytest.mark.parametrize(
    ("name", "type_", "expected"),
    [
        (SCHEMA, "schema", False),
        ("registry", "schema", True),  # owned by Base.metadata — must stay reflected
        ("public", "schema", True),
        (None, "schema", True),  # alembic's spelling of the default schema
        (SCHEMA, "table", True),  # a *table* named "serving" elsewhere is not the schema
    ],
)
def test_include_name_excludes_only_the_serving_schema(name, type_, expected):
    assert include_name(name, type_, {}) is expected


@pytest.fixture
async def committed_serving(test_engine: AsyncEngine) -> AsyncIterator[AsyncEngine]:
    """A committed `serving` schema in the test DB, the way the loader leaves prod.

    Committed rather than savepointed: ``alembic/env.py`` opens its own connection, so
    it sees only what is committed. Not one of ``declared_schemas()`` — the session
    teardown would never drop it — so this fixture drops it itself.
    """
    async with test_engine.begin() as conn:
        await conn.execute(text(f'DROP SCHEMA IF EXISTS "{SCHEMA}" CASCADE'))
        await conn.execute(text(f'CREATE SCHEMA "{SCHEMA}"'))
        await conn.run_sync(ServingBase.metadata.create_all)
    yield test_engine
    async with test_engine.begin() as conn:
        await conn.execute(text(f'DROP SCHEMA IF EXISTS "{SCHEMA}" CASCADE'))


def _serving_ops(migration_context: MigrationContext) -> list:
    """The upgrade ops autogenerate proposes for anything in `serving`."""
    script = produce_migrations(migration_context, Base.metadata)
    return [op for op in script.upgrade_ops.ops if getattr(op, "schema", None) == SCHEMA]


def _unfiltered_serving_ops(migration_context: MigrationContext) -> list:
    """The same comparison on the same connection, minus env.py's hook — the control."""
    bare = MigrationContext.configure(
        migration_context.connection,
        opts={"include_schemas": True, "target_metadata": Base.metadata},
    )
    return _serving_ops(bare)


def _ops_through_env_py() -> tuple[list, list]:
    """Run ``alembic/env.py`` itself; return its `serving` ops, filtered and not.

    Mirrors ``alembic.command.check``'s ``EnvironmentContext`` + ``run_env()``, but the
    work function compares instead of demanding the DB be at head (``test_engine``
    builds with ``create_all`` and drops ``alembic_version``). ``dont_mutate`` stops
    ``run_migrations`` creating a version table. No ``config_file_name``, so env.py's
    ``fileConfig`` never reconfigures the suite's logging.
    """
    config = Config()
    config.set_main_option("script_location", str(REPO_ROOT / "alembic"))
    script = ScriptDirectory.from_config(config)
    filtered: list = []
    unfiltered: list = []

    def work(_rev, migration_context: MigrationContext) -> list:
        filtered.extend(_serving_ops(migration_context))
        unfiltered.extend(_unfiltered_serving_ops(migration_context))
        return []

    with EnvironmentContext(config, script, fn=work, as_sql=False, dont_mutate=True):
        script.run_env()
    return filtered, unfiltered


async def test_env_py_autogenerate_proposes_nothing_for_serving(
    committed_serving: AsyncEngine, monkeypatch: pytest.MonkeyPatch
):
    """The behaviour #573 is about, through the real ``env.py``: a DB holding a
    `serving` schema yields no upgrade op touching it.

    The control runs on env.py's *own* connection, so a wrong DSN (a database with no
    `serving`) cannot make the empty answer pass vacuously."""
    # The root conftest blocks both DSNs, and env.py prefers the owner one.
    test_url = committed_serving.url.render_as_string(hide_password=False)
    monkeypatch.setenv("DATABASE_URL_OWNER", test_url)
    # env.py drives its own asyncio.run(); give it a thread with no running loop.
    filtered, unfiltered = await asyncio.to_thread(_ops_through_env_py)
    dropped = {op.table_name for op in unfiltered if isinstance(op, DropTableOp)}
    assert dropped == {table.name for table in ServingBase.metadata.tables.values()}
    assert filtered == []
