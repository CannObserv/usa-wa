"""Assert the append-only grant topology on the clearinghouse_core schema (#54).

No clearinghouse_core table is append-only since #412 PR F dropped the provenance spine
(``fetch_events``, ``raw_payloads``, ``citations``) whose REVOKEs this guarded; the raw
store's objects are write-once by construction instead. The guard stays for the next
table: it still forces the decision below.

`scripts/grants.sql` REVOKEs UPDATE/DELETE from the app role to make the
provenance ledger write-once (CR finding #1/#2). But step 5's
`ALTER DEFAULT PRIVILEGES` re-grants full DML on *future* clearinghouse_core
tables, so a newly-added table is born mutable and the REVOKE does not auto-apply
— the invariant silently regresses.

This guard closes that gap the way test_unit_ordering closes the systemd-ordering
gap: the intended per-table grant treatment is encoded as data, and the on-disk
table set (from Base.metadata) is cross-checked against it. Adding a
clearinghouse_core table without classifying it here fails the suite, forcing an
explicit "is this append-only?" decision — and an append-only table not present
in the corresponding REVOKE block in grants.sql fails too.

Pure file parse + metadata read — no DB, no applied grants (the test DB never
runs grants.sql); runs everywhere.
"""

import re
from pathlib import Path

import pytest

from clearinghouse_core.models import Base

REPO = Path(__file__).parent.parent.parent  # scripts/tests/ → repo
GRANTS = REPO / "scripts" / "grants.sql"
SCHEMA = "clearinghouse_core"

# Intended grant treatment per clearinghouse_core table, encoded as data.
#   revoke_update — the stored row is immutable (no app UPDATE).
#   revoke_delete — the row is permanent (no app DELETE).
# Mutable lookup tables carry neither revocation. A new table here forces an explicit row.
EXPECTED: dict[str, dict[str, bool]] = {
    "jurisdiction_types": {"revoke_update": False, "revoke_delete": False},
    "jurisdictions": {"revoke_update": False, "revoke_delete": False},
    "sources": {"revoke_update": False, "revoke_delete": False},
    # #178 job-run ledger: mutable by design — the harness opens an in-flight row
    # and UPDATEs it terminal when the job reports back, and old runs are
    # DELETE-able for retention. Operational telemetry, not a provenance ledger
    # row (nothing cites it), so neither revocation.
    "job_runs": {"revoke_update": False, "revoke_delete": False},
    # #180 source-coverage audit: mutable by design — a re-audit UPDATEs the
    # existing (source, dimension, range_start) row in place rather than minting a
    # second one, and a retracted claim is DELETE-able. It records what a feed
    # covers, not what a feed asserted, so nothing cites it; neither revocation.
    "source_coverage": {"revoke_update": False, "revoke_delete": False},
}


def _revoked_tables(privilege: str) -> set[str]:
    """Tables the app role has ``privilege`` REVOKEd on, parsed from grants.sql.

    Matches each ``REVOKE <privs> ON <tables> FROM`` block (privs and tables may
    span lines), keeps blocks whose privilege list includes ``privilege``, and
    collects the ``clearinghouse_core.<table>`` names from them.

    Comment lines are stripped first: the prose explaining the REVOKEs contains
    the words "REVOKE … ON" and would otherwise poison the cross-statement
    non-greedy match.
    """
    text = "\n".join(
        line for line in GRANTS.read_text().splitlines() if not line.lstrip().startswith("--")
    )
    tables: set[str] = set()
    for privs, target in re.findall(r"REVOKE\s+(.*?)\s+ON\s+(.*?)\s+FROM", text, re.DOTALL):
        granted = {p.strip().upper() for p in privs.split(",")}
        if privilege.upper() not in granted:
            continue
        tables.update(re.findall(rf"{SCHEMA}\.(\w+)", target))
    return tables


def _schema_tables() -> set[str]:
    """Production clearinghouse_core tables, by mapper.

    Filters on the mapped class's module so test-only tables that declare the
    same schema (e.g. ``FakeWidget`` in test_adapter_runner) don't pollute the
    set when those modules are imported in a full-suite run — the failure that
    a metadata-only scan produces. Schema ownership maps to the package, so
    every real clearinghouse_core-schema table is defined in clearinghouse_core.
    """
    names: set[str] = set()
    for mapper in Base.registry.mappers:
        table = mapper.local_table
        if table is None or table.schema != SCHEMA:
            continue
        if not mapper.class_.__module__.startswith("clearinghouse_core"):
            continue
        names.add(table.name)
    return names


def test_every_clearinghouse_core_table_is_classified():
    """Adding a clearinghouse_core table forces an append-only decision here."""
    assert _schema_tables() == set(EXPECTED)


@pytest.mark.parametrize("table", sorted(EXPECTED))
def test_revoke_update_matches_intent(table):
    revoked = table in _revoked_tables("UPDATE")
    assert revoked == EXPECTED[table]["revoke_update"]


@pytest.mark.parametrize("table", sorted(EXPECTED))
def test_revoke_delete_matches_intent(table):
    revoked = table in _revoked_tables("DELETE")
    assert revoked == EXPECTED[table]["revoke_delete"]


def test_insert_and_select_are_never_revoked():
    """The complement of write-once: only UPDATE/DELETE may ever be revoked. An
    over-broad REVOKE INSERT/SELECT would break a writer that the test-owner role (immune
    to grants) never exercises, so no other test would catch it — this guard does.
    """
    assert _revoked_tables("INSERT") == set()
    assert _revoked_tables("SELECT") == set()


def _statements() -> str:
    """grants.sql with its comment lines stripped — prose names dropped tables freely."""
    return "\n".join(
        line for line in GRANTS.read_text().splitlines() if not line.lstrip().startswith("--")
    )


def test_every_table_grants_sql_names_still_exists():
    """``usa-wa-migrate`` runs grants.sql after every ``alembic upgrade head``, and a GRANT or
    REVOKE on a table that no longer exists is an ERROR, not a no-op: a drop migration that
    left its table in this file would wedge the unit on the deploy that ran it (#412 PR F)."""
    named = set(re.findall(rf"\b{SCHEMA}\.(\w+)", _statements()))
    assert named <= _schema_tables(), f"grants.sql names dropped tables: {named - _schema_tables()}"


#: Created by grants.sql itself (``CREATE SCHEMA IF NOT EXISTS serving``) for the app role's
#: serving load (#313), so no model declares it.
SELF_CREATED_SCHEMAS = {"serving"}


def test_every_schema_grants_sql_names_is_declared():
    """The schema-level twin: a migration that drops a schema must leave this file in the
    same change (#314 step C did, for ``sync``; #412 PR F, for ``canonical``)."""
    lists = re.findall(r"\b(?:ON|IN) SCHEMA\s+([\w,\s]+?)\s+(?:TO|GRANT)\b", _statements())
    named = {name.strip() for group in lists for name in group.split(",")}
    declared = {t.schema for t in Base.metadata.tables.values() if t.schema}
    assert named, "the schema scan found nothing — the pattern no longer matches grants.sql"
    assert named <= declared | SELF_CREATED_SCHEMAS, (
        f"grants.sql grants on undeclared schemas: {named - declared - SELF_CREATED_SCHEMAS}"
    )
