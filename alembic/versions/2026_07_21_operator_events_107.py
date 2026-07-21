"""add operator_events succession-attestation table (#107)

Operator-attested mid-biennium succession events (death / resignation /
appointment) the WSL/SOS/PDC wires cannot supply. Event-shaped
(``departed`` / ``seated`` on an ``effective_date``); the span builders read
the non-superseded rows as an authoritative overlay. Backed by a first-class
``usa_wa_operator`` provenance Source (rows written alongside a FetchEvent +
RawPayload). Corrections append a new row and stamp the prior's
``superseded_by_id``.

No ``grants.sql`` change — same ``canonical`` schema, default privileges already
grant the app role DML.

Revision ID: f1a2b3c4d5e6
Revises: b3669b3ef3be
Create Date: 2026-07-21 00:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

import clearinghouse_core.db.ulid

# revision identifiers, used by Alembic.
revision: str = 'f1a2b3c4d5e6'
down_revision: Union[str, Sequence[str], None] = 'b3669b3ef3be'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        'operator_events',
        sa.Column('id', clearinghouse_core.db.ulid.ULID(), nullable=False),
        sa.Column('source', sa.String(length=64), nullable=False),
        sa.Column('source_id', sa.String(length=256), nullable=False),
        sa.Column('member_id', sa.String(length=128), nullable=False),
        sa.Column('kind', sa.String(length=16), nullable=False),
        sa.Column('reason', sa.String(length=32), nullable=False),
        sa.Column('seat_kind', sa.String(length=32), nullable=True),
        sa.Column('seat_discriminator', sa.String(length=64), nullable=True),
        sa.Column('effective_date', sa.Date(), nullable=False),
        sa.Column('evidence_url', sa.Text(), nullable=False),
        sa.Column('entered_by', sa.String(length=128), nullable=True),
        sa.Column('superseded_by_id', clearinghouse_core.db.ulid.ULID(), nullable=True),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.CheckConstraint(
            "kind IN ('departed', 'vacated', 'seated')", name='ck_operator_events_kind'
        ),
        sa.CheckConstraint(
            "(kind IN ('seated', 'vacated')"
            " AND seat_kind IS NOT NULL AND seat_discriminator IS NOT NULL)"
            " OR (kind = 'departed' AND seat_kind IS NULL AND seat_discriminator IS NULL)",
            name='ck_operator_events_seat_shape',
        ),
        sa.ForeignKeyConstraint(
            ['superseded_by_id'], ['canonical.operator_events.id'], ondelete='SET NULL'
        ),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('source', 'source_id', name='uq_operator_events_natural_key'),
        schema='canonical',
    )
    op.create_index(
        'ix_operator_events_member', 'operator_events', ['member_id'],
        unique=False, schema='canonical',
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index('ix_operator_events_member', table_name='operator_events', schema='canonical')
    op.drop_table('operator_events', schema='canonical')
