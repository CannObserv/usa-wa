"""v1.1 revisions — drop persons.birth_year; add canonical.bill_titles.

Driven by the OCD transformation review on 2026-05-28. See:
- docs/specs/2026-05-27-hybrid-legislative-ia.md §Changelog (v1 → v1.1)
- docs/specs/2026-05-27-power-map-integration.md §Power Map as the rich-attribute store
- CannObserv/power-map#165 (polymorphic lifecycle_events — birth/death move there)

Revision ID: 20260528_v1_1_titles
Revises: 20260527_canonical_init
Create Date: 2026-05-28
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

import clearinghouse_core.db.ulid

revision: str = "20260528_v1_1_titles"
down_revision: str | Sequence[str] | None = "20260527_canonical_init"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "canonical"


def upgrade() -> None:
    """Drop persons.birth_year; create canonical.bill_titles."""
    # Drop birth_year — lifecycle events defer to Power Map's planned schema
    # (CannObserv/power-map#165). Local Person carries identity essentials only.
    op.drop_column("persons", "birth_year", schema=SCHEMA)

    # Add canonical.bill_titles for the 1:N title relationship per Bill.
    op.create_table(
        "bill_titles",
        sa.Column("id", clearinghouse_core.db.ulid.ULID(), nullable=False),
        sa.Column("jurisdiction_id", sa.String(length=32), nullable=False),
        sa.Column("source", sa.String(length=64), nullable=False),
        sa.Column("source_id", sa.String(length=128), nullable=False),
        sa.Column("bill_id", clearinghouse_core.db.ulid.ULID(), nullable=False),
        sa.Column("title_text", sa.Text(), nullable=False),
        sa.Column("title_type", sa.String(length=32), nullable=False),
        sa.Column("chamber", sa.String(length=16), nullable=True),
        sa.Column("as_of_action", sa.String(length=64), nullable=True),
        sa.Column("language_code", sa.String(length=8), nullable=True),
        sa.Column("amendment_id", clearinghouse_core.db.ulid.ULID(), nullable=True),
        sa.Column("effective_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("replaced_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("is_current", sa.Boolean(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["amendment_id"],
            [f"{SCHEMA}.amendments.id"],
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["bill_id"],
            [f"{SCHEMA}.bills.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "jurisdiction_id", "source", "source_id", name="uq_bill_titles_natural_key"
        ),
        schema=SCHEMA,
    )
    op.create_index(
        op.f("ix_canonical_bill_titles_amendment_id"),
        "bill_titles",
        ["amendment_id"],
        unique=False,
        schema=SCHEMA,
    )
    op.create_index(
        op.f("ix_canonical_bill_titles_bill_id"),
        "bill_titles",
        ["bill_id"],
        unique=False,
        schema=SCHEMA,
    )
    op.create_index(
        op.f("ix_canonical_bill_titles_jurisdiction_id"),
        "bill_titles",
        ["jurisdiction_id"],
        unique=False,
        schema=SCHEMA,
    )


def downgrade() -> None:
    """Reverse: drop bill_titles; restore persons.birth_year as nullable int."""
    op.drop_index(
        op.f("ix_canonical_bill_titles_jurisdiction_id"),
        table_name="bill_titles",
        schema=SCHEMA,
    )
    op.drop_index(
        op.f("ix_canonical_bill_titles_bill_id"), table_name="bill_titles", schema=SCHEMA
    )
    op.drop_index(
        op.f("ix_canonical_bill_titles_amendment_id"),
        table_name="bill_titles",
        schema=SCHEMA,
    )
    op.drop_table("bill_titles", schema=SCHEMA)
    op.add_column(
        "persons",
        sa.Column("birth_year", sa.Integer(), nullable=True),
        schema=SCHEMA,
    )
