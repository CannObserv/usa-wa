"""v1.2 review revisions from the OCD spec review on 2026-05-28.

Changes:

- Bill: drop ``short_description`` + ``current_text``; add ``current_version_id`` FK
  to BillVersion. Per-version summary + text live on BillVersion now.

- BillVersion: add ``short_description`` + ``text`` + ``amendment_id`` FK.

- Amendment: add ``amendment_kind`` column for traditional / striking / substitute.

- LegislativeSession: add ``adjourned_sine_die_at`` column (sine die is an
  adjournment state, not a session classification — the classification vocab
  tightens to {regular | special | other} at the application layer, no DB enum
  change needed since it's a string column).

- New tables:
  - canonical.bill_version_links (1:N alternative representations per version)
  - canonical.bill_statutory_citations (extracted from bill text)
  - clearinghouse_core.notes (polymorphic editorial / staff notes)

Revision ID: 20260528_v1_2_review
Revises: 20260528_v1_1_titles
Create Date: 2026-05-28
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

import clearinghouse_core.db.ulid

revision: str = "20260528_v1_2_review"
down_revision: str | Sequence[str] | None = "20260528_v1_1_titles"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

CANONICAL = "canonical"
CHCORE = "clearinghouse_core"


def upgrade() -> None:
    """Apply v1.2 review revisions."""
    # --- Bill column moves ---
    op.drop_column("bills", "short_description", schema=CANONICAL)
    op.drop_column("bills", "current_text", schema=CANONICAL)
    op.add_column(
        "bills",
        sa.Column("current_version_id", clearinghouse_core.db.ulid.ULID(), nullable=True),
        schema=CANONICAL,
    )

    # --- BillVersion new columns ---
    op.add_column(
        "bill_versions",
        sa.Column("short_description", sa.Text(), nullable=True),
        schema=CANONICAL,
    )
    op.add_column(
        "bill_versions",
        sa.Column("text", sa.Text(), nullable=True),
        schema=CANONICAL,
    )
    op.add_column(
        "bill_versions",
        sa.Column("amendment_id", clearinghouse_core.db.ulid.ULID(), nullable=True),
        schema=CANONICAL,
    )
    op.create_index(
        op.f("ix_canonical_bill_versions_amendment_id"),
        "bill_versions",
        ["amendment_id"],
        unique=False,
        schema=CANONICAL,
    )
    op.create_foreign_key(
        "fk_bill_versions_amendment_id",
        "bill_versions",
        "amendments",
        ["amendment_id"],
        ["id"],
        source_schema=CANONICAL,
        referent_schema=CANONICAL,
        ondelete="SET NULL",
    )

    # Bills.current_version_id FK (Bill <-> BillVersion is circular, so use ALTER
    # ADD CONSTRAINT here rather than inline at create_table).
    op.create_foreign_key(
        "fk_bills_current_version_id",
        "bills",
        "bill_versions",
        ["current_version_id"],
        ["id"],
        source_schema=CANONICAL,
        referent_schema=CANONICAL,
        ondelete="SET NULL",
        use_alter=True,
    )

    # --- Amendment new column ---
    op.add_column(
        "amendments",
        sa.Column(
            "amendment_kind",
            sa.String(length=16),
            nullable=False,
            server_default="traditional",
        ),
        schema=CANONICAL,
    )
    # Drop the server_default after backfill — adapters set the value explicitly.
    op.alter_column(
        "amendments",
        "amendment_kind",
        server_default=None,
        schema=CANONICAL,
    )

    # --- LegislativeSession adjournment tracking ---
    op.add_column(
        "legislative_sessions",
        sa.Column("adjourned_sine_die_at", sa.DateTime(timezone=True), nullable=True),
        schema=CANONICAL,
    )

    # --- canonical.bill_version_links (1:N) ---
    op.create_table(
        "bill_version_links",
        sa.Column("id", clearinghouse_core.db.ulid.ULID(), nullable=False),
        sa.Column("jurisdiction_id", sa.String(length=32), nullable=False),
        sa.Column("source", sa.String(length=64), nullable=False),
        sa.Column("source_id", sa.String(length=128), nullable=False),
        sa.Column("bill_version_id", clearinghouse_core.db.ulid.ULID(), nullable=False),
        sa.Column("url", sa.String(length=2048), nullable=False),
        sa.Column("mime_type", sa.String(length=128), nullable=True),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("title", sa.String(length=256), nullable=True),
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
            ["bill_version_id"], [f"{CANONICAL}.bill_versions.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "jurisdiction_id",
            "source",
            "source_id",
            name="uq_bill_version_links_natural_key",
        ),
        schema=CANONICAL,
    )
    op.create_index(
        op.f("ix_canonical_bill_version_links_bill_version_id"),
        "bill_version_links",
        ["bill_version_id"],
        unique=False,
        schema=CANONICAL,
    )
    op.create_index(
        op.f("ix_canonical_bill_version_links_jurisdiction_id"),
        "bill_version_links",
        ["jurisdiction_id"],
        unique=False,
        schema=CANONICAL,
    )

    # --- canonical.bill_statutory_citations ---
    op.create_table(
        "bill_statutory_citations",
        sa.Column("id", clearinghouse_core.db.ulid.ULID(), nullable=False),
        sa.Column("jurisdiction_id", sa.String(length=32), nullable=False),
        sa.Column("source", sa.String(length=64), nullable=False),
        sa.Column("source_id", sa.String(length=128), nullable=False),
        sa.Column("bill_version_id", clearinghouse_core.db.ulid.ULID(), nullable=False),
        sa.Column("statute_section_id", clearinghouse_core.db.ulid.ULID(), nullable=True),
        sa.Column("raw_text", sa.String(length=256), nullable=False),
        sa.Column("text_offset_start", sa.Integer(), nullable=True),
        sa.Column("text_offset_end", sa.Integer(), nullable=True),
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
            ["bill_version_id"], [f"{CANONICAL}.bill_versions.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["statute_section_id"],
            [f"{CANONICAL}.statute_sections.id"],
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "jurisdiction_id",
            "source",
            "source_id",
            name="uq_bill_statutory_citations_natural_key",
        ),
        schema=CANONICAL,
    )
    op.create_index(
        op.f("ix_canonical_bill_statutory_citations_bill_version_id"),
        "bill_statutory_citations",
        ["bill_version_id"],
        unique=False,
        schema=CANONICAL,
    )
    op.create_index(
        op.f("ix_canonical_bill_statutory_citations_statute_section_id"),
        "bill_statutory_citations",
        ["statute_section_id"],
        unique=False,
        schema=CANONICAL,
    )
    op.create_index(
        op.f("ix_canonical_bill_statutory_citations_jurisdiction_id"),
        "bill_statutory_citations",
        ["jurisdiction_id"],
        unique=False,
        schema=CANONICAL,
    )

    # --- clearinghouse_core.notes (polymorphic) ---
    op.create_table(
        "notes",
        sa.Column("id", clearinghouse_core.db.ulid.ULID(), nullable=False),
        sa.Column("entity_type", sa.String(length=64), nullable=False),
        sa.Column("entity_id", clearinghouse_core.db.ulid.ULID(), nullable=False),
        sa.Column("note_kind", sa.String(length=32), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("author_person_id", clearinghouse_core.db.ulid.ULID(), nullable=True),
        sa.Column("author_organization_id", clearinghouse_core.db.ulid.ULID(), nullable=True),
        sa.Column("effective_at", sa.DateTime(timezone=True), nullable=True),
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
        sa.PrimaryKeyConstraint("id"),
        schema=CHCORE,
    )
    op.create_index(
        op.f("ix_clearinghouse_core_notes_entity_id"),
        "notes",
        ["entity_id"],
        unique=False,
        schema=CHCORE,
    )
    op.create_index(
        op.f("ix_clearinghouse_core_notes_entity_type"),
        "notes",
        ["entity_type"],
        unique=False,
        schema=CHCORE,
    )


def downgrade() -> None:
    """Reverse v1.2 review revisions."""
    op.drop_index(
        op.f("ix_clearinghouse_core_notes_entity_type"),
        table_name="notes",
        schema=CHCORE,
    )
    op.drop_index(
        op.f("ix_clearinghouse_core_notes_entity_id"),
        table_name="notes",
        schema=CHCORE,
    )
    op.drop_table("notes", schema=CHCORE)

    for ix in (
        "ix_canonical_bill_statutory_citations_jurisdiction_id",
        "ix_canonical_bill_statutory_citations_statute_section_id",
        "ix_canonical_bill_statutory_citations_bill_version_id",
    ):
        op.drop_index(op.f(ix), table_name="bill_statutory_citations", schema=CANONICAL)
    op.drop_table("bill_statutory_citations", schema=CANONICAL)

    for ix in (
        "ix_canonical_bill_version_links_jurisdiction_id",
        "ix_canonical_bill_version_links_bill_version_id",
    ):
        op.drop_index(op.f(ix), table_name="bill_version_links", schema=CANONICAL)
    op.drop_table("bill_version_links", schema=CANONICAL)

    op.drop_column("legislative_sessions", "adjourned_sine_die_at", schema=CANONICAL)
    op.drop_column("amendments", "amendment_kind", schema=CANONICAL)

    op.drop_constraint(
        "fk_bills_current_version_id", "bills", schema=CANONICAL, type_="foreignkey"
    )
    op.drop_constraint(
        "fk_bill_versions_amendment_id",
        "bill_versions",
        schema=CANONICAL,
        type_="foreignkey",
    )
    op.drop_index(
        op.f("ix_canonical_bill_versions_amendment_id"),
        table_name="bill_versions",
        schema=CANONICAL,
    )
    op.drop_column("bill_versions", "amendment_id", schema=CANONICAL)
    op.drop_column("bill_versions", "text", schema=CANONICAL)
    op.drop_column("bill_versions", "short_description", schema=CANONICAL)

    op.drop_column("bills", "current_version_id", schema=CANONICAL)
    op.add_column(
        "bills",
        sa.Column("current_text", sa.Text(), nullable=True),
        schema=CANONICAL,
    )
    op.add_column(
        "bills",
        sa.Column("short_description", sa.Text(), nullable=True),
        schema=CANONICAL,
    )
