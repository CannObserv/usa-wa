"""v1.3 add canonical.bill_supplements + bill_actions.supplement_id FK.

LegiScan review #1 follow-up (2026-05-30). Adds the per-bill-version
supplementary-document entity (Bill Analysis / Bill Report / Fiscal Note /
Bill Summary) authored by non-partisan committee staff and regulatory
agencies. See ``clearinghouse_domain_legislative.bills.BillSupplement`` for the
design rationale. Pairs with ``BillAction.supplement_id`` FK so each
supplement publication generates a lifecycle-log row that points to the
authoritative artifact.

Revision ID: 20260530_bill_supplements
Revises: 20260530_doc_identifiers
Create Date: 2026-05-30
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

import clearinghouse_core.db.ulid

revision: str = "20260530_bill_supplements"
down_revision: str | Sequence[str] | None = "20260530_doc_identifiers"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

CANONICAL = "canonical"


def upgrade() -> None:
    op.create_table(
        "bill_supplements",
        sa.Column("id", clearinghouse_core.db.ulid.ULID(), nullable=False),
        sa.Column("jurisdiction_id", sa.String(length=32), nullable=False),
        sa.Column("source", sa.String(length=64), nullable=False),
        sa.Column("source_id", sa.String(length=128), nullable=False),
        sa.Column("bill_version_id", clearinghouse_core.db.ulid.ULID(), nullable=False),
        sa.Column("bill_id", clearinghouse_core.db.ulid.ULID(), nullable=False),
        sa.Column("supplement_kind", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=True),
        sa.Column("revision_sequence", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("title", sa.String(length=512), nullable=True),
        sa.Column(
            "author_organization_id", clearinghouse_core.db.ulid.ULID(), nullable=True
        ),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("url", sa.String(length=2048), nullable=True),
        sa.Column("mime_type", sa.String(length=128), nullable=True),
        sa.Column("text", sa.Text(), nullable=True),
        sa.Column(
            "structured_data",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
        sa.Column("archival_url", sa.String(length=2048), nullable=True),
        sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True),
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
            ["bill_version_id"],
            [f"{CANONICAL}.bill_versions.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["bill_id"], [f"{CANONICAL}.bills.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["author_organization_id"],
            [f"{CANONICAL}.organizations.id"],
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "jurisdiction_id",
            "source",
            "source_id",
            name="uq_bill_supplements_natural_key",
        ),
        sa.UniqueConstraint(
            "bill_version_id",
            "supplement_kind",
            "status",
            "revision_sequence",
            name="uq_bill_supplements_content_key",
        ),
        schema=CANONICAL,
    )
    op.create_index(
        op.f("ix_canonical_bill_supplements_author_organization_id"),
        "bill_supplements",
        ["author_organization_id"],
        unique=False,
        schema=CANONICAL,
    )
    op.create_index(
        op.f("ix_canonical_bill_supplements_bill_id"),
        "bill_supplements",
        ["bill_id"],
        unique=False,
        schema=CANONICAL,
    )
    op.create_index(
        op.f("ix_canonical_bill_supplements_bill_version_id"),
        "bill_supplements",
        ["bill_version_id"],
        unique=False,
        schema=CANONICAL,
    )
    op.create_index(
        op.f("ix_canonical_bill_supplements_jurisdiction_id"),
        "bill_supplements",
        ["jurisdiction_id"],
        unique=False,
        schema=CANONICAL,
    )

    op.add_column(
        "bill_actions",
        sa.Column("supplement_id", clearinghouse_core.db.ulid.ULID(), nullable=True),
        schema=CANONICAL,
    )
    op.create_index(
        op.f("ix_canonical_bill_actions_supplement_id"),
        "bill_actions",
        ["supplement_id"],
        unique=False,
        schema=CANONICAL,
    )
    op.create_foreign_key(
        "fk_bill_actions_supplement_id",
        "bill_actions",
        "bill_supplements",
        ["supplement_id"],
        ["id"],
        source_schema=CANONICAL,
        referent_schema=CANONICAL,
        ondelete="SET NULL",
    )


def downgrade() -> None:
    op.drop_constraint(
        "fk_bill_actions_supplement_id",
        "bill_actions",
        schema=CANONICAL,
        type_="foreignkey",
    )
    op.drop_index(
        op.f("ix_canonical_bill_actions_supplement_id"),
        table_name="bill_actions",
        schema=CANONICAL,
    )
    op.drop_column("bill_actions", "supplement_id", schema=CANONICAL)

    for ix in (
        "ix_canonical_bill_supplements_jurisdiction_id",
        "ix_canonical_bill_supplements_bill_version_id",
        "ix_canonical_bill_supplements_bill_id",
        "ix_canonical_bill_supplements_author_organization_id",
    ):
        op.drop_index(op.f(ix), table_name="bill_supplements", schema=CANONICAL)
    op.drop_table("bill_supplements", schema=CANONICAL)
