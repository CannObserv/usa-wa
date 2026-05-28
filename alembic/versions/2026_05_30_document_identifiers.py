"""v1.3 add clearinghouse_core.document_identifiers (polymorphic).

Polymorphic identifier mapping for bill texts, amendments, and similar artifacts
that carry rich identifiers below the Bill level. See the model docstring in
``clearinghouse_core.provenance.DocumentIdentifier`` for the design rationale —
WA's Code Reviser bill-text IDs (``H-0043.1``), Code Reviser amendment IDs
(``S-5276.3/26``), committee amendment forms (``1066 AMH CPB CLOD 295``), and
lifecycle-tagged identifiers (``EHB 1941.PL``) are the motivating use case.

Revision ID: 20260530_doc_identifiers
Revises: 20260529_vote_action_link
Create Date: 2026-05-30
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

import clearinghouse_core.db.ulid

revision: str = "20260530_doc_identifiers"
down_revision: str | Sequence[str] | None = "20260529_vote_action_link"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

CHCORE = "clearinghouse_core"


def upgrade() -> None:
    op.create_table(
        "document_identifiers",
        sa.Column("id", clearinghouse_core.db.ulid.ULID(), nullable=False),
        sa.Column("jurisdiction_id", sa.String(length=32), nullable=False),
        sa.Column("source", sa.String(length=64), nullable=False),
        sa.Column("source_id", sa.String(length=128), nullable=False),
        sa.Column("entity_type", sa.String(length=32), nullable=False),
        sa.Column("entity_id", clearinghouse_core.db.ulid.ULID(), nullable=False),
        sa.Column("scheme", sa.String(length=64), nullable=False),
        sa.Column("value", sa.String(length=256), nullable=False),
        sa.Column(
            "parsed_components",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
        sa.Column("verified_at", sa.DateTime(timezone=True), nullable=True),
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
        sa.UniqueConstraint(
            "jurisdiction_id",
            "source",
            "source_id",
            name="uq_document_identifiers_natural_key",
        ),
        sa.UniqueConstraint(
            "entity_type",
            "entity_id",
            "scheme",
            name="uq_document_identifiers_entity_scheme",
        ),
        sa.UniqueConstraint(
            "jurisdiction_id",
            "scheme",
            "value",
            name="uq_document_identifiers_jurisdiction_scheme_value",
        ),
        schema=CHCORE,
    )
    op.create_index(
        op.f("ix_clearinghouse_core_document_identifiers_entity_id"),
        "document_identifiers",
        ["entity_id"],
        unique=False,
        schema=CHCORE,
    )
    op.create_index(
        op.f("ix_clearinghouse_core_document_identifiers_entity_type"),
        "document_identifiers",
        ["entity_type"],
        unique=False,
        schema=CHCORE,
    )
    op.create_index(
        op.f("ix_clearinghouse_core_document_identifiers_jurisdiction_id"),
        "document_identifiers",
        ["jurisdiction_id"],
        unique=False,
        schema=CHCORE,
    )


def downgrade() -> None:
    for ix in (
        "ix_clearinghouse_core_document_identifiers_jurisdiction_id",
        "ix_clearinghouse_core_document_identifiers_entity_type",
        "ix_clearinghouse_core_document_identifiers_entity_id",
    ):
        op.drop_index(op.f(ix), table_name="document_identifiers", schema=CHCORE)
    op.drop_table("document_identifiers", schema=CHCORE)
