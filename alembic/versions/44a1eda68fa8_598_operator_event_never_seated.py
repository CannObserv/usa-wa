"""598: operator events can attest a term never served (never_seated)

Statutory term edges (#598) expose John Lovick's 2007-08 listing. He resigned on
2007-01-05, before the term opened on 2007-01-08, and never served it. That makes the
fourth never-seated case, so #546's curated table becomes an operator attestation (spec
§ 1.5). The kind is person-scoped like ``departed``, so it names no seat. Its
``effective_date`` is the first day of the term never served.

Both CHECK constraints are re-created to admit it; every existing row still satisfies
them. App-role DML only: no new column, and grants are unchanged.

Revision ID: 44a1eda68fa8
Revises: f9213b85b417
Create Date: 2026-11-25 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = '44a1eda68fa8'
down_revision: Union[str, Sequence[str], None] = 'f9213b85b417'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_KIND = "ck_operator_events_kind"
_SHAPE = "ck_operator_events_seat_shape"


def _recreate(kinds: str, seatless: str) -> None:
    op.drop_constraint(_KIND, "operator_events", schema="registry", type_="check")
    op.drop_constraint(_SHAPE, "operator_events", schema="registry", type_="check")
    op.create_check_constraint(_KIND, "operator_events", f"kind IN ({kinds})", schema="registry")
    op.create_check_constraint(
        _SHAPE,
        "operator_events",
        "(kind IN ('seated', 'vacated') AND seat_kind IS NOT NULL"
        " AND seat_discriminator IS NOT NULL)"
        f" OR (kind IN ({seatless}) AND seat_kind IS NULL AND seat_discriminator IS NULL)",
        schema="registry",
    )


def upgrade() -> None:
    """Admit ``never_seated``: a kind, and seatless."""
    _recreate("'departed', 'vacated', 'seated', 'never_seated'", "'departed', 'never_seated'")


def downgrade() -> None:
    """Restore the three-kind checks. This fails while any ``never_seated`` row exists:
    retract or delete those first, deliberately, because they are the record."""
    _recreate("'departed', 'vacated', 'seated'", "'departed'")
