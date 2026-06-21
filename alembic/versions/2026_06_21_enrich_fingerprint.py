"""enrich re-propagation: outbox payload_hash + enrich_fingerprint table (#34)

Adds the local-fingerprint machinery that lets the sidecar re-enrich an
already-anchored row when the carry payload it holds drifts from what was last
sent to PM:

- ``sync.powermap_outbox.payload_hash`` — nullable; the carry-payload hash an
  ENRICH entry carries from enqueue to settle (CREATE/UPDATE leave it null).
- ``sync.powermap_enrich_fingerprint`` — the last enrich payload hash settled per
  source row; the anchored-cohort reconcile re-enqueues an ENRICH only when the
  current hash differs from this stamp.

No new schema (``sync`` already exists), so ``scripts/grants.sql`` already covers
the new table via ALTER DEFAULT PRIVILEGES — no grant change required.

Revision ID: a1c7e9d2f3b4
Revises: b88a5888e2ba
Create Date: 2026-06-21
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

import clearinghouse_core.db.ulid

revision: str = "a1c7e9d2f3b4"
down_revision: str | Sequence[str] | None = "b88a5888e2ba"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SYNC = "sync"


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        "powermap_outbox",
        sa.Column("payload_hash", sa.String(length=64), nullable=True),
        schema=SYNC,
    )
    op.create_table(
        "powermap_enrich_fingerprint",
        sa.Column("id", clearinghouse_core.db.ulid.ULID(), nullable=False),
        sa.Column("entity_type", sa.String(length=64), nullable=False),
        sa.Column("local_id", clearinghouse_core.db.ulid.ULID(), nullable=False),
        sa.Column("payload_hash", sa.String(length=64), nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "entity_type", "local_id", name="uq_powermap_enrich_fingerprint_row"
        ),
        schema=SYNC,
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table("powermap_enrich_fingerprint", schema=SYNC)
    op.drop_column("powermap_outbox", "payload_hash", schema=SYNC)
