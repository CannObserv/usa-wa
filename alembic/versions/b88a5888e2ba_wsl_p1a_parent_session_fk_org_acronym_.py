"""WSL P1a — parent session FK + org acronym/phone.

Three additive columns supporting the WSL SOAP P1a first cut
(docs/plans/2026-06-18-wsl-soap-adapter-p1a.md):

- ``canonical.legislative_sessions.parent_legislative_session_id`` — self-FK
  enabling a biennium → regular/special parent/child hierarchy. Bills span
  regular and special sessions within a biennium, so the biennium is modeled
  as a parent session (``classification='biennium'``) with the child regular
  and special sessions pointing to it via this column.
- ``canonical.organizations.acronym`` — canonical acronym for an Org (e.g.
  ``APP`` for House Appropriations). PM Org observations support a list of
  acronyms; this column tracks the single canonical value.
- ``canonical.organizations.phone`` — primary phone contact, stored as the
  source string (no E.164 normalization at write time). PM Org observations
  accept this as a ``phone`` contact_method.

Revision ID: b88a5888e2ba
Revises: f5f1bd9f84ae
Create Date: 2026-06-18 16:04:18.464522
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

import clearinghouse_core.db.ulid

revision: str = "b88a5888e2ba"
down_revision: str | Sequence[str] | None = "f5f1bd9f84ae"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        "legislative_sessions",
        sa.Column(
            "parent_legislative_session_id",
            clearinghouse_core.db.ulid.ULID(),
            nullable=True,
        ),
        schema="canonical",
    )
    op.create_index(
        "ix_canonical_legislative_sessions_parent_legislative_session_id",
        "legislative_sessions",
        ["parent_legislative_session_id"],
        unique=False,
        schema="canonical",
    )
    op.create_foreign_key(
        "fk_legislative_sessions_parent_legislative_session_id",
        "legislative_sessions",
        "legislative_sessions",
        ["parent_legislative_session_id"],
        ["id"],
        source_schema="canonical",
        referent_schema="canonical",
        ondelete="RESTRICT",
    )
    op.add_column(
        "organizations",
        sa.Column("acronym", sa.String(length=64), nullable=True),
        schema="canonical",
    )
    op.add_column(
        "organizations",
        sa.Column("phone", sa.String(length=64), nullable=True),
        schema="canonical",
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("organizations", "phone", schema="canonical")
    op.drop_column("organizations", "acronym", schema="canonical")
    op.drop_constraint(
        "fk_legislative_sessions_parent_legislative_session_id",
        "legislative_sessions",
        schema="canonical",
        type_="foreignkey",
    )
    op.drop_index(
        "ix_canonical_legislative_sessions_parent_legislative_session_id",
        table_name="legislative_sessions",
        schema="canonical",
    )
    op.drop_column(
        "legislative_sessions",
        "parent_legislative_session_id",
        schema="canonical",
    )
