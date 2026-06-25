"""split archived vs deleted lifecycle axes (#42)

Rename the overloaded ``retired_at`` tombstone to ``archived_at`` and add a
separate terminal ``deleted_at``, mirroring Power Map's orthogonal lifecycle axes
(reversible archival vs terminal delete).

**Data direction is deliberate.** The old ``retired_at`` held *both* PM archival
(reversible, live anchor) and genuine-delete/merge-orphan (terminal, dead anchor)
tombstones, which cannot be disambiguated historically. Renaming every existing
value into ``archived_at`` is the **self-correcting** direction: a row that was
really a genuine delete now re-enters the reconcile cohort (filtered on
``deleted_at IS NULL``), gets re-fetched, 404s, and ``_heal_dead_anchor`` re-stamps
it ``deleted_at`` (clearing ``archived_at``) on the next cycle. The reverse
(everything → ``deleted_at``) would NOT self-correct — deleted rows are excluded
from reconcile and never re-fetched, freezing the #42 bug for any pre-existing
archival row. ``deleted_at`` therefore starts empty.

Revision ID: d552d384b788
Revises: 136d33aa41b8
Create Date: 2026-06-25

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'd552d384b788'
down_revision: Union[str, Sequence[str], None] = '136d33aa41b8'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_TABLES = ('assignments', 'organizations', 'persons', 'roles')


def upgrade() -> None:
    """Upgrade schema."""
    for table in _TABLES:
        # Preserve existing tombstones as *archival* (safe self-correcting direction).
        op.alter_column(table, 'retired_at', new_column_name='archived_at', schema='canonical')
        op.add_column(
            table,
            sa.Column('deleted_at', sa.DateTime(timezone=True), nullable=True),
            schema='canonical',
        )


def downgrade() -> None:
    """Downgrade schema.

    Lossy: ``deleted_at`` values are dropped and ``archived_at`` collapses back to
    ``retired_at``. A row that had only ``deleted_at`` set (a post-migration genuine
    delete) reverts to a live row — acceptable for a downgrade, which is a
    development escape hatch, not a routine path.
    """
    for table in _TABLES:
        op.drop_column(table, 'deleted_at', schema='canonical')
        op.alter_column(table, 'archived_at', new_column_name='retired_at', schema='canonical')
