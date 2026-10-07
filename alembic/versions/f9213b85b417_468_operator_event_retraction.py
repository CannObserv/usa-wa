"""468: operator events can be retracted

An operator event can be wrong with nothing to correct it to. The 2026-08-17 roster
backfill parsed Betty Sue Morris's 1996 resignation onto Jim Springer's 1993 row, so
member 656 carries a departure he never made; a supersede needs a corrected event for the
same member, and there is none. ``retracted_at`` takes such a row out of the current set
and keeps it (provenance is append-only, #54).

A row is superseded or retracted, never both: a superseded row's correction is what
stands, so retracting the prior would say nothing about it. Every existing row satisfies
the check — ``retracted_at`` is new and NULL.

App-role DML only: ``grants.sql`` already grants the app role UPDATE on every
``registry`` table, and a new column inherits its table's grants.

Revision ID: f9213b85b417
Revises: f9efb48842d6
Create Date: 2026-10-07 18:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision: str = 'f9213b85b417'
down_revision: Union[str, Sequence[str], None] = 'f9efb48842d6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_CHECK = "ck_operator_events_superseded_or_retracted"


def upgrade() -> None:
    """Add ``retracted_at`` and the superseded-or-retracted check."""
    op.add_column(
        "operator_events",
        sa.Column("retracted_at", sa.DateTime(timezone=True), nullable=True),
        schema="registry",
    )
    op.create_check_constraint(
        _CHECK,
        "operator_events",
        "superseded_by_id IS NULL OR retracted_at IS NULL",
        schema="registry",
    )


def downgrade() -> None:
    """Drop the check and the column — a retraction is lost with it, so the row reads
    as current again."""
    op.drop_constraint(_CHECK, "operator_events", schema="registry", type_="check")
    op.drop_column("operator_events", "retracted_at", schema="registry")
