"""Role-type catalog mirror table (power-map#268, usa-wa#68).

Local cache of PM's ``role_types`` catalog (``GET /api/v1/role-types``) so the sync
descriptor can decide a Role observation's shape (seat vs title) at runtime from PM's
own catalog instead of a hardcoded slug map. Refreshed by the sidecar catalog sync;
no seed (PM is the source of truth). Canonical schema → the app role auto-grants DML
via ALTER DEFAULT PRIVILEGES (scripts/grants.sql needs no change).

Revision ID: e1a7c4b9d2f6
Revises: c7d9e1f3a5b7
Create Date: 2026-07-05
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

import clearinghouse_core.db.ulid

revision: str = "e1a7c4b9d2f6"
down_revision: str | Sequence[str] | None = "c7d9e1f3a5b7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "role_types",
        sa.Column("id", clearinghouse_core.db.ulid.ULID(), nullable=False),
        sa.Column("pm_role_type_id", clearinghouse_core.db.ulid.ULID(), nullable=True),
        sa.Column("slug", sa.String(length=64), nullable=False),
        sa.Column("display_name", sa.String(length=128), nullable=False),
        sa.Column("is_seat", sa.Boolean(), nullable=False),
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
        sa.UniqueConstraint("slug", name="uq_role_types_slug"),
        schema="canonical",
    )
    op.create_index(
        "ix_canonical_role_types_pm_role_type_id",
        "role_types",
        ["pm_role_type_id"],
        unique=False,
        schema="canonical",
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(
        "ix_canonical_role_types_pm_role_type_id",
        table_name="role_types",
        schema="canonical",
    )
    op.drop_table("role_types", schema="canonical")
