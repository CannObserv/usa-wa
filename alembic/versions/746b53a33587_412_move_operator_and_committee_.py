"""412 PR A: move operator and committee-succession events to registry

The canonical tier retires (#412), and these two tables are the curated state
it holds that the #302 pipeline still reads: ``operator_events`` (553 rows,
read by the nightly ``dbt build`` through ``usa_wa_pipeline.operator_read``) and
``committee_succession_events`` (165). Both are human-entered attestations, the
same kind of state as ``registry.adjudications``, so they move beside it (#412
Q1) before PR F drops the ``canonical`` schema.

``SET SCHEMA`` moves each table with its rows, constraints, indexes and
grants. Each table's only foreign key is its own ``superseded_by_id``, and no
other table or view references either one (verified 2026-09-25), so nothing is
left pointing into ``canonical``. ``grants.sql`` already grants the app role
DML on every ``registry`` table, and the migrate unit re-runs it after this.

Revision ID: 746b53a33587
Revises: 5079afab4030
Create Date: 2026-09-25 18:00:23.878939

"""
from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = '746b53a33587'
down_revision: Union[str, Sequence[str], None] = '5079afab4030'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_TABLES = ("operator_events", "committee_succession_events")


def upgrade() -> None:
    """Move both tables from ``canonical`` to ``registry``."""
    for table in _TABLES:
        op.execute(f'ALTER TABLE canonical."{table}" SET SCHEMA registry')


def downgrade() -> None:
    """Move both tables back to ``canonical``."""
    for table in _TABLES:
        op.execute(f'ALTER TABLE registry."{table}" SET SCHEMA canonical')
