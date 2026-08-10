"""index the span-kind expression on assignments (CR #196 finding 40)

``GET /api/v1/assignments?span_kind=`` (#184) filters on
``split_part(source_id, ':', 2)`` — the span *kind* lives in position 2 of the span key
``<member>:<kind>:<scope>:<start>``. No plain index serves an expression, so every
filtered page was a **Seq Scan plus a Sort**, which defeats the premise the route's
keyset pagination is built on ("index-seekable at any depth"). Measured on a 200k-row
probe: ``Seq Scan + Sort`` → ``Index Scan``, no sort.

Indexed on the expression rather than on a ``roles.role_type`` proxy. The two agree
exactly in production today (``chamber-house``↔``state_representative``,
``chamber-senate``↔``state_senator``, ``committee``↔``committee_member``,
``party``↔``party_member``, with no role_type spanning two kinds) — but ``role_type`` is
a PM-synced classifier with a recorded drift incident (#110), while the span key is
generated locally and deterministically by the span builders. Buying an index by
filtering on the churning discriminator would be the wrong half of that trade.

``id`` is the trailing column so the route's ``ORDER BY id`` keyset is satisfied by the
same index instead of by a sort.

Index-only, additive, no data change: safe to replay and to roll back. The
``array_length(...) = 4`` part-count guard in the query stays a cheap recheck over the
rows the index already narrowed — it is a correctness guard against legacy 2-part
``source_id`` values, not a selectivity one.

Revision ID: 0598c2e839ef
Revises: 02bb603b7702
Create Date: 2026-08-10
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0598c2e839ef"
down_revision: str | Sequence[str] | None = "02bb603b7702"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

INDEX_NAME = "ix_assignments_span_kind"


def upgrade() -> None:
    """Upgrade schema."""
    op.create_index(
        INDEX_NAME,
        "assignments",
        [sa.text("split_part(source_id, ':', 2)"), sa.text("id")],
        schema="canonical",
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(INDEX_NAME, table_name="assignments", schema="canonical")
