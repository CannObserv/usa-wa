"""add Source.retention_policy (#54)

Per-source payload-retention contract: ``operational_cache`` (default — bodies
eligible for the eventual RawPayload GC) vs ``archival`` (provenance-critical;
a future GC must never delete them). Stored as a String (FetchStatus precedent),
so adding a value later is a data change, not DDL.

The ``server_default`` backfills every existing row to ``operational_cache``,
preserving today's behaviour. No grants.sql change — clearinghouse_core schema,
the app role's existing DML grants cover the altered table.

Revision ID: c3f1a9e6d2b4
Revises: b2d5a8f3c0e1
Create Date: 2026-06-29 00:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'c3f1a9e6d2b4'
down_revision: Union[str, Sequence[str], None] = 'b2d5a8f3c0e1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        'sources',
        sa.Column(
            'retention_policy',
            sa.String(length=32),
            nullable=False,
            server_default='operational_cache',
        ),
        schema='clearinghouse_core',
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('sources', 'retention_policy', schema='clearinghouse_core')
