"""v1.3 Bill.classification + BillSponsorship.sponsored_at.

uscongress review #1 follow-up (2026-06-01).

- ``Bill.classification: text(32) nullable`` (OQ14) — OCD-aligned semantic
  classification orthogonal to bill_type's prefix-encoded form. Adapter
  derives from bill_type or source signal. Allows querying "all resolutions
  this session" without parsing bill_type strings.
- ``BillSponsorship.sponsored_at: timestamptz nullable`` (OQ8) — when the
  sponsor signed on. Federal cosponsors[].sponsored_at populates this;
  recovers original-cosponsor inference by comparing to Bill.introduced_at.

Revision ID: 20260601_bill_class_sponsored_at
Revises: 20260531_bill_chamber_fk
Create Date: 2026-06-01
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260601_bill_class_sponsored_at"
down_revision: str | Sequence[str] | None = "20260531_bill_chamber_fk"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

CANONICAL = "canonical"


def upgrade() -> None:
    op.add_column(
        "bills",
        sa.Column("classification", sa.String(length=32), nullable=True),
        schema=CANONICAL,
    )
    op.add_column(
        "bill_sponsorships",
        sa.Column("sponsored_at", sa.DateTime(timezone=True), nullable=True),
        schema=CANONICAL,
    )


def downgrade() -> None:
    op.drop_column("bill_sponsorships", "sponsored_at", schema=CANONICAL)
    op.drop_column("bills", "classification", schema=CANONICAL)
