"""One-row-per-PM-anchor invariant: partial unique indexes on the anchor columns (usa-wa#86).

The #84 crash loop was armed by 98 pairs of ``canonical.assignments`` rows sharing
one ``pm_assignment_id`` (legacy pre-#79 rows + #79 span rows, both auto-attached to
the same PM assignment). The "one local row per PM assignment" invariant was documented
but enforced nowhere: nothing stopped a second row from being stamped with an existing
anchor, and the read-side ``local_match`` ``scalar_one_or_none`` raised
``MultipleResultsFound`` on the duplicate, poisoning the whole reconcile/feed apply path.

This replaces the plain lookup index on each of the four PM anchor columns
(``pm_person_id`` / ``pm_organization_id`` / ``pm_role_id`` / ``pm_assignment_id``) with
a **partial unique** index ``WHERE <col> IS NOT NULL`` — a duplicate now fails loudly at
write time (one parked outbox entry) instead of silently arming a reconcile crash loop
days later. NULL anchors (unsynced rows) still coexist freely. The unique index also
serves the lookups the dropped plain index did, so no read path regresses.

A pre-flight guard aborts with a readable message if any duplicate anchor still exists —
``CREATE UNIQUE INDEX`` would otherwise fail with a raw low-level error. The #84 data
repair (``migrate_pdc_spans`` / ``migrate_sponsor_spans``) must run first; prod was
verified duplicate-free before this migration was authored.

Revision ID: b3f7c2a9d4e1
Revises: a7e1d9551f31
Create Date: 2026-07-15
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "b3f7c2a9d4e1"
down_revision: str | Sequence[str] | None = "a7e1d9551f31"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "canonical"

#: (table, anchor column, old plain index, new partial-unique index).
_ANCHORS = [
    ("persons", "pm_person_id", "ix_canonical_persons_pm_person_id", "uq_persons_pm_person_id"),
    (
        "organizations",
        "pm_organization_id",
        "ix_canonical_organizations_pm_organization_id",
        "uq_organizations_pm_organization_id",
    ),
    ("roles", "pm_role_id", "ix_canonical_roles_pm_role_id", "uq_roles_pm_role_id"),
    (
        "assignments",
        "pm_assignment_id",
        "ix_canonical_assignments_pm_assignment_id",
        "uq_assignments_pm_assignment_id",
    ),
]


def _guard_no_duplicates() -> None:
    """Abort with a readable message if any anchor already has duplicates.

    ``CREATE UNIQUE INDEX`` on a column with duplicates fails with a raw
    ``UniqueViolation`` that names only the offending key. This surfaces which
    table/column is dirty and points at the repair to run first (#84's
    ``migrate_pdc_spans`` / ``migrate_sponsor_spans``).
    """
    conn = op.get_bind()
    for table, column, _old, _new in _ANCHORS:
        dupes = conn.execute(
            sa.text(
                f"SELECT count(*) FROM ("  # noqa: S608 — table/column from a fixed literal list
                f"  SELECT {column} FROM {SCHEMA}.{table}"
                f"  WHERE {column} IS NOT NULL GROUP BY {column} HAVING count(*) > 1"
                f") d"
            )
        ).scalar_one()
        if dupes:
            raise RuntimeError(
                f"{SCHEMA}.{table}.{column} has {dupes} duplicate anchor(s); "
                "repair them first (usa-wa#84: migrate_pdc_spans / migrate_sponsor_spans) "
                "before applying the one-row-per-anchor unique index (#86)."
            )


def upgrade() -> None:
    """Replace each plain anchor index with a partial unique index."""
    _guard_no_duplicates()
    for table, column, old_index, new_index in _ANCHORS:
        op.drop_index(old_index, table_name=table, schema=SCHEMA)
        op.create_index(
            new_index,
            table,
            [column],
            unique=True,
            schema=SCHEMA,
            postgresql_where=sa.text(f"{column} IS NOT NULL"),
        )


def downgrade() -> None:
    """Restore the plain (non-unique) lookup index on each anchor column."""
    for table, column, old_index, new_index in _ANCHORS:
        op.drop_index(new_index, table_name=table, schema=SCHEMA)
        op.create_index(old_index, table, [column], unique=False, schema=SCHEMA)
