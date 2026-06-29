"""Assert the append-only grant topology on the clearinghouse_core provenance spine (#54).

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
# raw_payloads is the deliberate split: immutable bytes (no UPDATE) but GC-able
# (DELETE kept for the retention GC). Mutable lookup/editorial tables carry
# neither revocation. A new table here forces an explicit row.
EXPECTED: dict[str, dict[str, bool]] = {
    "jurisdiction_types": {"revoke_update": False, "revoke_delete": False},
    "jurisdiction_relationship_types": {"revoke_update": False, "revoke_delete": False},
    "jurisdictions": {"revoke_update": False, "revoke_delete": False},
    "jurisdiction_relationships": {"revoke_update": False, "revoke_delete": False},
    "sources": {"revoke_update": False, "revoke_delete": False},
    "fetch_events": {"revoke_update": True, "revoke_delete": True},
    "raw_payloads": {"revoke_update": True, "revoke_delete": False},
    "citations": {"revoke_update": True, "revoke_delete": True},
    "notes": {"revoke_update": False, "revoke_delete": False},
    "document_identifiers": {"revoke_update": False, "revoke_delete": False},
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
