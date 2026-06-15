"""outbox status: add UNAVAILABLE (max-attempts dead-letter, #5)

Widens the ``ck_powermap_outbox_status`` CHECK to admit the UNAVAILABLE state,
a terminal-but-re-drivable status for entries that exhaust the transport-failure
retry cap (PM unreachable for too long). Distinct from REJECTED, which is PM
explicitly refusing the payload.

Revision ID: d2e3f4a5b6c7
Revises: c1f2a3b4d5e6
Create Date: 2026-06-15
"""

from alembic import op

revision = "d2e3f4a5b6c7"
down_revision = "c1f2a3b4d5e6"
branch_labels = None
depends_on = None

SYNC = "sync"
_CONSTRAINT = "ck_powermap_outbox_status"
_TABLE = "powermap_outbox"


def upgrade() -> None:
    op.drop_constraint(_CONSTRAINT, _TABLE, schema=SYNC, type_="check")
    op.create_check_constraint(
        _CONSTRAINT,
        _TABLE,
        "status IN ('PENDING', 'DELIVERED', 'REJECTED', 'UNAVAILABLE')",
        schema=SYNC,
    )


def downgrade() -> None:
    op.drop_constraint(_CONSTRAINT, _TABLE, schema=SYNC, type_="check")
    op.create_check_constraint(
        _CONSTRAINT,
        _TABLE,
        "status IN ('PENDING', 'DELIVERED', 'REJECTED')",
        schema=SYNC,
    )
