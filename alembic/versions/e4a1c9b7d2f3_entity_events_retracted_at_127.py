"""entity_events.retracted_at — C3 retract-of-superseded producer support (usa-wa#127).

Adds a nullable local marker set when the committee-event producer emits an
``op=retract`` for an anchored entity event whose operator attestation was
superseded and not reasserted. Local-only (never mirrored from PM), so the
read-mirror sync leaves it untouched.

Revision ID: e4a1c9b7d2f3
Revises: 2365a8eceb9e
Create Date: 2026-07-28
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "e4a1c9b7d2f3"
down_revision: str | Sequence[str] | None = "2365a8eceb9e"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "canonical"


def upgrade() -> None:
    op.add_column(
        "entity_events",
        sa.Column("retracted_at", sa.DateTime(timezone=True), nullable=True),
        schema=SCHEMA,
    )


def downgrade() -> None:
    op.drop_column("entity_events", "retracted_at", schema=SCHEMA)
