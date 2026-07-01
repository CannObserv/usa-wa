"""drop redundant standalone fetch_events.source_id index (#59 CR)

The composite ``ix_clearinghouse_core_fetch_events_dedup`` (added in the prior
revision) leads with ``source_id``, so it is a covering prefix for both
source_id-only lookups and ``AdapterRunner._find_fresh_fetch_event``'s
``(source_id, resource_id)`` query. The single-column
``ix_clearinghouse_core_fetch_events_source_id`` is therefore redundant — dead
write-amplification/storage on an append-only, daily-growing table. Drop it.

The ``resource_id`` standalone index stays (resource_id is not a prefix of the
composite). No grants.sql change.

Revision ID: e5f3c9d2a6b8
Revises: d4e2b8c1f5a7
Create Date: 2026-07-01 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'e5f3c9d2a6b8'
down_revision: Union[str, Sequence[str], None] = 'd4e2b8c1f5a7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.drop_index(
        op.f('ix_clearinghouse_core_fetch_events_source_id'),
        table_name='fetch_events',
        schema='clearinghouse_core',
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.create_index(
        op.f('ix_clearinghouse_core_fetch_events_source_id'),
        'fetch_events',
        ['source_id'],
        unique=False,
        schema='clearinghouse_core',
    )
