"""add organization_names dated-name child table (#45)

Mirror PM's dated org names (power-map#239): a name valid over an
``[effective_start, effective_end)`` window. ``Organization.name`` stays the
resolved current scalar; this child table is the history/association surface
queried when historical WSL data references a *former* committee name.

Read-mirror only (org descriptor ``upsert_from_pm`` → ``sync_org_names``); the
rename producer (#46) emits to PM and the mirror brings it back. No ``grants.sql``
change — same ``canonical`` schema, default privileges already grant the app role
DML.

Revision ID: a1c4f7e2b9d0
Revises: 4aa429d354a1
Create Date: 2026-06-26 14:10:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

import clearinghouse_core.db.ulid

# revision identifiers, used by Alembic.
revision: str = 'a1c4f7e2b9d0'
down_revision: Union[str, Sequence[str], None] = '4aa429d354a1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        'organization_names',
        sa.Column('id', clearinghouse_core.db.ulid.ULID(), nullable=False),
        sa.Column('source', sa.String(length=64), nullable=False),
        sa.Column('source_id', sa.String(length=128), nullable=False),
        sa.Column('organization_id', clearinghouse_core.db.ulid.ULID(), nullable=False),
        sa.Column('name', sa.String(length=512), nullable=False),
        sa.Column('name_type', sa.String(length=32), nullable=False),
        sa.Column('is_canonical', sa.Boolean(), nullable=False),
        sa.Column('effective_start', sa.Date(), nullable=True),
        sa.Column('effective_end', sa.Date(), nullable=True),
        sa.Column('pm_org_name_id', clearinghouse_core.db.ulid.ULID(), nullable=True),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['organization_id'], ['canonical.organizations.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('source', 'source_id', name='uq_organization_names_natural_key'),
        schema='canonical',
    )
    op.create_index(
        op.f('ix_canonical_organization_names_organization_id'),
        'organization_names', ['organization_id'], unique=False, schema='canonical',
    )
    op.create_index(
        op.f('ix_canonical_organization_names_pm_org_name_id'),
        'organization_names', ['pm_org_name_id'], unique=False, schema='canonical',
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(
        op.f('ix_canonical_organization_names_pm_org_name_id'),
        table_name='organization_names', schema='canonical',
    )
    op.drop_index(
        op.f('ix_canonical_organization_names_organization_id'),
        table_name='organization_names', schema='canonical',
    )
    op.drop_table('organization_names', schema='canonical')
