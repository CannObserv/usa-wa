"""index fetch_events(source_id, resource_id, content_hash) for archival dedup (#59)

Backs ``AdapterRunner._payload_already_archived``'s per-fetch lookup, which
filters exactly ``(source_id, resource_id, content_hash)``. The single-column
``source_id``/``resource_id`` indexes only narrow the scan, leaving
``content_hash`` sequentially filtered over a subset that grows with fetch
history (fetch_events is append-only + daily cadence). Pure optimization, no
behaviour change.

No grants.sql change — clearinghouse_core schema, an index needs no grant and the
app role's existing DML grants on fetch_events are untouched.

Revision ID: d4e2b8c1f5a7
Revises: c3f1a9e6d2b4
Create Date: 2026-07-01 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'd4e2b8c1f5a7'
down_revision: Union[str, Sequence[str], None] = 'c3f1a9e6d2b4'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_index(
        'ix_clearinghouse_core_fetch_events_dedup',
        'fetch_events',
        ['source_id', 'resource_id', 'content_hash'],
        unique=False,
        schema='clearinghouse_core',
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(
        'ix_clearinghouse_core_fetch_events_dedup',
        table_name='fetch_events',
        schema='clearinghouse_core',
    )
