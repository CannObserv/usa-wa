"""outbox status: add SUPERSEDED (a rejection a later PENDING entry replaced, #258)

Widens ``ck_powermap_outbox_status`` to admit SUPERSEDED. REJECTED is terminal on
purpose — a blind retry repeats the rejection — so when the defect is fixed in code
and the cohort re-enqueued, the old rejections stay REJECTED forever and hold the
backlog count static. That is exactly the reading the sidecar's rise-alert treats as
"nothing new", so a genuinely new rejection hides in the pile. SUPERSEDED retains the
row (the incident stays legible) while taking it out of the operator's to-do count.

Revision ID: a1b2c3d4e5f6
Revises: 27c8373439a4
Create Date: 2026-08-22
"""

from alembic import op

revision = "a1b2c3d4e5f6"
down_revision = "27c8373439a4"
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
        "status IN ('PENDING', 'DELIVERED', 'REJECTED', 'UNAVAILABLE', 'SUPERSEDED')",
        schema=SYNC,
    )


def downgrade() -> None:
    # Narrowing would violate the CHECK on any row already moved, so fold them back to the
    # status they carried before — the rows are still rejections, just settled ones.
    op.execute(
        f"UPDATE {SYNC}.{_TABLE} SET status = 'REJECTED' WHERE status = 'SUPERSEDED'"  # noqa: S608
    )
    op.drop_constraint(_CONSTRAINT, _TABLE, schema=SYNC, type_="check")
    op.create_check_constraint(
        _CONSTRAINT,
        _TABLE,
        "status IN ('PENDING', 'DELIVERED', 'REJECTED', 'UNAVAILABLE')",
        schema=SYNC,
    )
