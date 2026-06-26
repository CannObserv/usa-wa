"""add organization_acronyms child table (#47)

Mirror PM's org acronyms (``OrgDetail.acronyms`` — a list distinct from
``names``). ``Organization.acronym`` stays the resolved current scalar; this child
table is the history/association surface queried when historical WSL data
references a *former* committee acronym.

Sibling to the #45 organization_names table but thinner: PM's ``OrgAcronym`` is
``{id, acronym, is_canonical}`` only — no ``name_type``, no dated window.

Read-mirror only (org descriptor ``upsert_from_pm`` → ``sync_org_acronyms``); the
rename producer (#46) emits to PM and the mirror brings it back. No ``grants.sql``
change — same ``canonical`` schema, default privileges already grant the app role
DML.

Revision ID: b2d5a8f3c0e1
Revises: a1c4f7e2b9d0
Create Date: 2026-06-26 15:30:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

import clearinghouse_core.db.ulid

# revision identifiers, used by Alembic.
revision: str = 'b2d5a8f3c0e1'
down_revision: Union[str, Sequence[str], None] = 'a1c4f7e2b9d0'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        'organization_acronyms',
        sa.Column('id', clearinghouse_core.db.ulid.ULID(), nullable=False),
        sa.Column('source', sa.String(length=64), nullable=False),
        sa.Column('source_id', sa.String(length=128), nullable=False),
        sa.Column('organization_id', clearinghouse_core.db.ulid.ULID(), nullable=False),
        sa.Column('acronym', sa.String(length=64), nullable=False),
        sa.Column('is_canonical', sa.Boolean(), nullable=False),
        sa.Column('pm_org_acronym_id', clearinghouse_core.db.ulid.ULID(), nullable=True),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['organization_id'], ['canonical.organizations.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('source', 'source_id', name='uq_organization_acronyms_natural_key'),
        schema='canonical',
    )
    op.create_index(
        op.f('ix_canonical_organization_acronyms_organization_id'),
        'organization_acronyms', ['organization_id'], unique=False, schema='canonical',
    )
    op.create_index(
        op.f('ix_canonical_organization_acronyms_pm_org_acronym_id'),
        'organization_acronyms', ['pm_org_acronym_id'], unique=False, schema='canonical',
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(
        op.f('ix_canonical_organization_acronyms_pm_org_acronym_id'),
        table_name='organization_acronyms', schema='canonical',
    )
    op.drop_index(
        op.f('ix_canonical_organization_acronyms_organization_id'),
        table_name='organization_acronyms', schema='canonical',
    )
    op.drop_table('organization_acronyms', schema='canonical')
