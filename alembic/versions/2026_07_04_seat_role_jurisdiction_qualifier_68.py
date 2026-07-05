"""Seat-Role: roles.jurisdiction_id + qualifier + split unique index (usa-wa#68).

Aligns local ``canonical.roles`` with Power Map's seat model (power-map#261/#263):
a legislative seat is a durable Role keyed on the structural tuple
``(organization_id, role_type, jurisdiction_id, qualifier)`` so a produced seat
observation attaches to PM's 147 seats instead of minting a duplicate.

- ``roles.jurisdiction_id`` — nullable FK → the seat's LD jurisdiction (the
  seat's enduring district identity; NULL for non-districted roles). Distinct
  from the org-level binding-root jurisdiction dropped in the 2026-06-09
  decoupling.
- ``roles.qualifier`` — PM ``qualifier`` ("Position 1"/"Position 2" for the two
  WA House seats sharing an LD; NULL for a Senate seat and non-seat roles).
- Split the single ``uq_roles_org_name`` unique constraint into two partial
  unique indexes mirroring PM: districted seats keyed on
  ``(org, role_type, jurisdiction, qualifier)`` (NULLS NOT DISTINCT so a NULL
  qualifier is one-per-district); non-districted roles keep ``(org, name)``.

Revision ID: c7d9e1f3a5b7
Revises: f6a4d1c3e7b9
Create Date: 2026-07-04
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

import clearinghouse_core.db.ulid

revision: str = "c7d9e1f3a5b7"
down_revision: str | Sequence[str] | None = "f6a4d1c3e7b9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        "roles",
        sa.Column("jurisdiction_id", clearinghouse_core.db.ulid.ULID(), nullable=True),
        schema="canonical",
    )
    op.add_column(
        "roles",
        sa.Column("qualifier", sa.String(length=64), nullable=True),
        schema="canonical",
    )
    op.create_index(
        "ix_canonical_roles_jurisdiction_id",
        "roles",
        ["jurisdiction_id"],
        unique=False,
        schema="canonical",
    )
    op.create_foreign_key(
        "fk_roles_jurisdiction_id",
        "roles",
        "jurisdictions",
        ["jurisdiction_id"],
        ["id"],
        source_schema="canonical",
        referent_schema="clearinghouse_core",
        ondelete="RESTRICT",
    )
    # Replace the full (org, name) unique constraint with two partial indexes.
    op.drop_constraint("uq_roles_org_name", "roles", schema="canonical", type_="unique")
    op.create_index(
        "uq_roles_org_name",
        "roles",
        ["organization_id", "name"],
        unique=True,
        schema="canonical",
        postgresql_where=sa.text("jurisdiction_id IS NULL"),
    )
    op.create_index(
        "uq_roles_seat",
        "roles",
        ["organization_id", "role_type", "jurisdiction_id", "qualifier"],
        unique=True,
        schema="canonical",
        postgresql_where=sa.text("jurisdiction_id IS NOT NULL"),
        postgresql_nulls_not_distinct=True,
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index("uq_roles_seat", table_name="roles", schema="canonical")
    op.drop_index("uq_roles_org_name", table_name="roles", schema="canonical")
    op.create_unique_constraint(
        "uq_roles_org_name",
        "roles",
        ["organization_id", "name"],
        schema="canonical",
    )
    op.drop_constraint("fk_roles_jurisdiction_id", "roles", schema="canonical", type_="foreignkey")
    op.drop_index("ix_canonical_roles_jurisdiction_id", table_name="roles", schema="canonical")
    op.drop_column("roles", "qualifier", schema="canonical")
    op.drop_column("roles", "jurisdiction_id", schema="canonical")
