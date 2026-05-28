"""v1.3 bill chamber refs as FK to canonical.organizations.

LegiScan review #2 follow-up (2026-05-31). Replaces ``Bill.originating_chamber``
and ``Bill.current_chamber`` text(16) enum columns (``house``/``senate``/
``unicameral``) with ULID FK columns to ``canonical.organizations``. Chambers
are first-class Organizations (org_type='chamber'); chamber refs are FKs
throughout. ``BillAction.chamber`` and ``VoteEvent.chamber`` stay as text
denorms — those entities already carry the authoritative FK
(``acting_organization_id``, ``context_organization_id``).

No data migration needed: usa-wa hasn't started ingesting yet, so dropping +
adding columns is safe.

Revision ID: 20260531_bill_chamber_fk
Revises: 20260530_bill_supplements
Create Date: 2026-05-31
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

import clearinghouse_core.db.ulid

revision: str = "20260531_bill_chamber_fk"
down_revision: str | Sequence[str] | None = "20260530_bill_supplements"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

CANONICAL = "canonical"


def upgrade() -> None:
    op.add_column(
        "bills",
        sa.Column(
            "originating_chamber_id",
            clearinghouse_core.db.ulid.ULID(),
            nullable=False,
        ),
        schema=CANONICAL,
    )
    op.add_column(
        "bills",
        sa.Column(
            "current_chamber_id",
            clearinghouse_core.db.ulid.ULID(),
            nullable=True,
        ),
        schema=CANONICAL,
    )
    op.create_index(
        op.f("ix_canonical_bills_originating_chamber_id"),
        "bills",
        ["originating_chamber_id"],
        unique=False,
        schema=CANONICAL,
    )
    op.create_index(
        op.f("ix_canonical_bills_current_chamber_id"),
        "bills",
        ["current_chamber_id"],
        unique=False,
        schema=CANONICAL,
    )
    op.create_foreign_key(
        "fk_bills_originating_chamber_id",
        "bills",
        "organizations",
        ["originating_chamber_id"],
        ["id"],
        source_schema=CANONICAL,
        referent_schema=CANONICAL,
        ondelete="RESTRICT",
    )
    op.create_foreign_key(
        "fk_bills_current_chamber_id",
        "bills",
        "organizations",
        ["current_chamber_id"],
        ["id"],
        source_schema=CANONICAL,
        referent_schema=CANONICAL,
        ondelete="RESTRICT",
    )

    op.drop_column("bills", "current_chamber", schema=CANONICAL)
    op.drop_column("bills", "originating_chamber", schema=CANONICAL)


def downgrade() -> None:
    op.add_column(
        "bills",
        sa.Column("originating_chamber", sa.String(length=16), nullable=False),
        schema=CANONICAL,
    )
    op.add_column(
        "bills",
        sa.Column("current_chamber", sa.String(length=16), nullable=True),
        schema=CANONICAL,
    )
    op.drop_constraint(
        "fk_bills_current_chamber_id", "bills", schema=CANONICAL, type_="foreignkey"
    )
    op.drop_constraint(
        "fk_bills_originating_chamber_id", "bills", schema=CANONICAL, type_="foreignkey"
    )
    op.drop_index(
        op.f("ix_canonical_bills_current_chamber_id"),
        table_name="bills",
        schema=CANONICAL,
    )
    op.drop_index(
        op.f("ix_canonical_bills_originating_chamber_id"),
        table_name="bills",
        schema=CANONICAL,
    )
    op.drop_column("bills", "current_chamber_id", schema=CANONICAL)
    op.drop_column("bills", "originating_chamber_id", schema=CANONICAL)
