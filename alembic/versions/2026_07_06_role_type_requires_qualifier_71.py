"""Add role_types.requires_qualifier (power-map#273, usa-wa#71).

Power Map #273 adds an ENFORCED ``role_types.requires_qualifier`` flag: a
districted-seat observation of a ``requires_qualifier`` role type that arrives
without a ``qualifier`` is rejected (``qualifier_required``) rather than minting a
positionless seat (#267). The local catalog mirror follows suit so the
:class:`RoleDescriptor` can refuse such an observation pre-flight. Additive column,
``server_default false`` so existing rows (and any that predate the next catalog
sync) default to unconstrained.

Revision ID: a7e1d9551f31
Revises: f2b8d5c0e3a9
Create Date: 2026-07-06
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "a7e1d9551f31"
down_revision: str | Sequence[str] | None = "f2b8d5c0e3a9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        "role_types",
        sa.Column(
            "requires_qualifier",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
        schema="canonical",
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("role_types", "requires_qualifier", schema="canonical")
