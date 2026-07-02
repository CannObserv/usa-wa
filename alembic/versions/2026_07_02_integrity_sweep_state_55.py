"""add clearinghouse_core.integrity_sweep_state — rolling-sweep cursor (#55)

Persists the integrity sweep's ULID watermark so a routine run verifies a bounded
byte-slice of the RawPayload archive and resumes past it next run, wrapping at the
tail. Keeps per-run cost flat as the #39 archival docket volume grows, instead of
one O(all-payloads) re-hash racing ``TimeoutStartSec=``. See the model docstring in
``clearinghouse_core.sweep_state.IntegritySweepState``.

The cursor lives here (not on RawPayload) because #54 ``REVOKE UPDATE`` makes the
payload tables append-only for the app role. No grants.sql change: the app role's
ALTER DEFAULT PRIVILEGES on clearinghouse_core auto-grants DML on this new table,
and the #54 REVOKE is scoped to fetch_events/raw_payloads/citations only.

Revision ID: f6a4d1c3e7b9
Revises: e5f3c9d2a6b8
Create Date: 2026-07-02
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

import clearinghouse_core.db.ulid

revision: str = "f6a4d1c3e7b9"
down_revision: str | Sequence[str] | None = "e5f3c9d2a6b8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

CHCORE = "clearinghouse_core"


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "integrity_sweep_state",
        sa.Column("id", clearinghouse_core.db.ulid.ULID(), nullable=False),
        sa.Column("scope", sa.String(length=64), nullable=False),
        sa.Column("cursor", clearinghouse_core.db.ulid.ULID(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("scope", name="uq_integrity_sweep_state_scope"),
        schema=CHCORE,
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table("integrity_sweep_state", schema=CHCORE)
