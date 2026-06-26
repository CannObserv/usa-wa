"""add org active axis (#43)

Add ``organizations.active`` — PM's third lifecycle axis (the operationally-
live-vs-dissolved domain flag, ``organizations.active``; power-map#240). Orgs only
(not person/role/assignment). ``server_default true`` backfills existing rows as
live. Unlike ``archived_at``/``deleted_at`` (#42) this is NOT a hide-from-reads
gate — it never enters ``live_only``; the read-mirror lives in the org descriptor's
``upsert_from_pm`` (PM authority).

Revision ID: 4aa429d354a1
Revises: d552d384b788
Create Date: 2026-06-26 13:26:12.924507

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '4aa429d354a1'
down_revision: Union[str, Sequence[str], None] = 'd552d384b788'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        'organizations',
        sa.Column('active', sa.Boolean(), server_default=sa.text('true'), nullable=False),
        schema='canonical',
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('organizations', 'active', schema='canonical')
