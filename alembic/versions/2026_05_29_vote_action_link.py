"""VoteEvent.originating_bill_action_id traceability (OCD review #2 item #16).

Adds a nullable FK from canonical.vote_events to canonical.bill_actions so a
vote can cite the specific action it resulted from (OCD's VoteEvent.bill_action
pattern). Required for "the vote that produced this action" traceability and
for round-tripping OCD VoteEvent.bill_action linkage.

Revision ID: 20260529_vote_action_link
Revises: 20260528_v1_2_review
Create Date: 2026-05-29
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

import clearinghouse_core.db.ulid

revision: str = "20260529_vote_action_link"
down_revision: str | Sequence[str] | None = "20260528_v1_2_review"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

CANONICAL = "canonical"


def upgrade() -> None:
    op.add_column(
        "vote_events",
        sa.Column(
            "originating_bill_action_id",
            clearinghouse_core.db.ulid.ULID(),
            nullable=True,
        ),
        schema=CANONICAL,
    )
    op.create_index(
        op.f("ix_canonical_vote_events_originating_bill_action_id"),
        "vote_events",
        ["originating_bill_action_id"],
        unique=False,
        schema=CANONICAL,
    )
    op.create_foreign_key(
        "fk_vote_events_originating_bill_action_id",
        "vote_events",
        "bill_actions",
        ["originating_bill_action_id"],
        ["id"],
        source_schema=CANONICAL,
        referent_schema=CANONICAL,
        ondelete="SET NULL",
    )


def downgrade() -> None:
    op.drop_constraint(
        "fk_vote_events_originating_bill_action_id",
        "vote_events",
        schema=CANONICAL,
        type_="foreignkey",
    )
    op.drop_index(
        op.f("ix_canonical_vote_events_originating_bill_action_id"),
        table_name="vote_events",
        schema=CANONICAL,
    )
    op.drop_column("vote_events", "originating_bill_action_id", schema=CANONICAL)
