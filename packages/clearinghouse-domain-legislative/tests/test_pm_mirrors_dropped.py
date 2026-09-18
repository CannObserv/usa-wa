"""The four PM read-mirrors are gone, model and table (#314 step C).

`OrganizationName`, `OrganizationAcronym`, `EntityEvent` and `RoleType` mirrored
Power Map state into `canonical`. Every one of them had exactly one writer — the
sync sidecar's read-mirror — and no reader outside it, which is why #314 marked
them `retired` rather than `declared`: a declared table is waiting to be built, a
retired one is waiting to be dropped.

Step B deleted the writer. That left four tables holding a frozen copy of PM's
state as of 2026-09-08 (323 + 240 + 467 + 13 rows), maintained by nothing, in the
schema the serving projection reads from. This is the drop.

**Not gated on power-map#500.** The four anchor columns on the LIVE canonical
tables (`persons.pm_person_id` and its three siblings) are, because PM must
archive against them first. The `pm_*` columns on these four go with their own
tables and answer to nobody — which is the whole reason step C splits here.
"""

from __future__ import annotations

import pytest
from sqlalchemy import text

import clearinghouse_domain_legislative  # noqa: F401  (registers every domain model)
from clearinghouse_core.models import Base

#: `canonical.<name>` for each mirror, and the schema the sidecar owned.
DROPPED_TABLES = (
    "canonical.organization_names",
    "canonical.organization_acronyms",
    "canonical.entity_events",
    "canonical.role_types",
)
DROPPED_SCHEMA = "sync"


def test_no_model_declares_a_retired_mirror() -> None:
    """The classes are deleted, not merely unmapped.

    The package import above is what makes this assertion mean anything:
    `Base.metadata` holds only what has been imported, so without it the set is
    empty and the test passes against a tree where all four still exist.
    """
    declared = {f"{t.schema}.{t.name}" for t in Base.metadata.tables.values() if t.schema}
    assert declared.isdisjoint(DROPPED_TABLES), sorted(declared & set(DROPPED_TABLES))


@pytest.mark.parametrize("dotted", DROPPED_TABLES)
@pytest.mark.db
async def test_the_table_is_gone_at_head(db_session, dotted: str) -> None:
    """A head-migrated database has no such table.

    The model half above would pass on its own the moment the class is deleted;
    only this half says the migration ran.
    """
    schema, name = dotted.split(".")
    found = (
        await db_session.execute(
            text(
                "select 1 from information_schema.tables "
                "where table_schema = :s and table_name = :t"
            ),
            {"s": schema, "t": name},
        )
    ).scalar()
    assert found is None, f"{dotted} still exists at head"


@pytest.mark.db
async def test_the_sync_schema_is_gone_at_head(db_session) -> None:
    """The sidecar's whole schema, not just its tables.

    An empty `sync` schema left behind would keep `declared_schemas`' legacy
    entry load-bearing forever and read to an operator as a thing that might
    still fill up. The historical migrations that CREATE it are immutable, so it
    exists mid-chain and must be dropped at the end of the chain rather than
    edited out of the beginning.
    """
    found = (
        await db_session.execute(
            text("select 1 from information_schema.schemata where schema_name = :s"),
            {"s": DROPPED_SCHEMA},
        )
    ).scalar()
    assert found is None, "the sync schema still exists at head"
