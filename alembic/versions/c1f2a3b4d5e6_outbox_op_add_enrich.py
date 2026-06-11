"""outbox op: add ENRICH (enrich-on-match, power-map#198)

Widens the ``ck_powermap_outbox_op`` CHECK to admit the ENRICH operation, which
attaches usa-wa identifiers/names to an already-matched, identifier-less PM
entity (keyed on its ``pm_*_id`` internal identifier type).

Revision ID: c1f2a3b4d5e6
Revises: 8d3f5cb3248f
Create Date: 2026-06-11
"""

from alembic import op

revision = "c1f2a3b4d5e6"
down_revision = "8d3f5cb3248f"
branch_labels = None
depends_on = None

SYNC = "sync"
_CONSTRAINT = "ck_powermap_outbox_op"
_TABLE = "powermap_outbox"


def upgrade() -> None:
    op.drop_constraint(_CONSTRAINT, _TABLE, schema=SYNC, type_="check")
    op.create_check_constraint(
        _CONSTRAINT, _TABLE, "op IN ('CREATE', 'UPDATE', 'ENRICH')", schema=SYNC
    )


def downgrade() -> None:
    op.drop_constraint(_CONSTRAINT, _TABLE, schema=SYNC, type_="check")
    op.create_check_constraint(
        _CONSTRAINT, _TABLE, "op IN ('CREATE', 'UPDATE')", schema=SYNC
    )
