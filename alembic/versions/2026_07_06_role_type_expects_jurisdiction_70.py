"""Rename role_types.is_seat → expects_jurisdiction (power-map#271, usa-wa#70).

Power Map 0.7.0 retired the "seat" composite noun for the field vocabulary
Role Type / Jurisdiction / Qualifier, renaming the ``GET /api/v1/role-types`` item
field ``is_seat`` → ``expects_jurisdiction`` (same semantics: advisory hint that the
office is normally attached with a jurisdiction). The local mirror follows suit.
Pure column rename — no data change (values are preserved verbatim).

Revision ID: f2b8d5c0e3a9
Revises: e1a7c4b9d2f6
Create Date: 2026-07-06
"""

from collections.abc import Sequence

from alembic import op

revision: str = "f2b8d5c0e3a9"
down_revision: str | Sequence[str] | None = "e1a7c4b9d2f6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.alter_column(
        "role_types",
        "is_seat",
        new_column_name="expects_jurisdiction",
        schema="canonical",
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.alter_column(
        "role_types",
        "expects_jurisdiction",
        new_column_name="is_seat",
        schema="canonical",
    )
