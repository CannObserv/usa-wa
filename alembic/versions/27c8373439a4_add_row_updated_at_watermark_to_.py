"""conditional GET: add the local-clock watermark (usa-wa#247)

Adds ``sync.powermap_conditional_get_state.row_updated_at`` — the local row's LWW
clock as of the fetch that stored the validator beside it. The anchored-cohort
reconcile compares the row's current clock against it: advanced means a local-only
change PM has not seen, so the validator is withheld and the full body taken, which
lets ``apply_record``'s local-newer branch run and enqueue the push. Without it a
locally-edited anchored row 304s forever and never reaches PM.

Nullable with no backfill, deliberately: a validator stored before #247 has no
watermark, and the reconcile verifies rather than trusts. So the first pass after this
migration re-fetches the whole anchored cohort once, stamping as it goes — the cost
is one full GET per row, once (request count is unchanged; a 304 already spends a
round-trip), and it is also what pushes the changes stranded up to now (the #226
roster-succession corrections). Those unknown-watermark fetches are excluded from the
``local_newer_forced`` signal, so the first cycle after this reports the pending pushes
rather than the whole cohort.

Revision ID: 27c8373439a4
Revises: 0598c2e839ef
Create Date: 2026-08-18
"""

import sqlalchemy as sa
from alembic import op

revision = "27c8373439a4"
down_revision = "0598c2e839ef"
branch_labels = None
depends_on = None

SYNC = "sync"
_TABLE = "powermap_conditional_get_state"
_COLUMN = "row_updated_at"


def upgrade() -> None:
    op.add_column(
        _TABLE,
        sa.Column(_COLUMN, sa.DateTime(timezone=True), nullable=True),
        schema=SYNC,
    )


def downgrade() -> None:
    op.drop_column(_TABLE, _COLUMN, schema=SYNC)
