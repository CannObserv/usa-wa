"""add clearinghouse_core.source_coverage — coverage as data (#180)

``sources`` records how a feed is *configured* (reliability, cache TTL, retention) but
nothing about what it **covers**: no range, no audit date, no known gaps. Those facts
lived as module constants declared independently across adapter packages
(``DEFAULT_ELECTION_FLOOR = 2008`` in three files, ``SWEEP_FLOOR_YEAR = 1991`` in two),
and the load-bearing fact that the votewa filings export retired at 2018 existed only as
prose in ``docs/ARCHITECTURE.md``. ``docs/ARCHITECTURE.md`` § *Audit before you build*
already required the audit; its output had nowhere to land except a comment.

One row per ``(source_id, dimension, range_start)``. ``dimension`` names the **axis** of
the feed rather than the unit, because one source can publish several with different
bounds (WSL serves ``sponsor_roster`` from 1991-92 but ``committee_membership`` only from
1999-00). ``range_start`` / ``range_end`` are strings since the unit varies with the
dimension — a bare election year (``2008``) or a WA biennium label (``1991-92``); a NULL
``range_end`` means open-ended.

``status`` is CHECK-constrained to ``verified`` | ``assumed`` | ``absent``, the same
vocabulary discipline ``job_runs.outcome`` carries (#178). ``absent`` is the one the
design turns on: it lets a known gap be a *fact* the system can answer with rather than
the silence a missing row is indistinguishable from. The unique key is per-``range_start``
precisely so a served span and an ``absent`` span coexist on one dimension — votewa
filings are ``verified`` 2008–2018 and ``absent`` 2020–onward, and both are true.

``source_id`` CASCADEs (a coverage claim has no meaning without its source);
``evidence_citation_id`` SET NULLs (losing the probe's citation must not delete the claim).

Additive: no existing table changes, and nothing reads this until an adapter's
``coverage.py`` seeds it. No grants.sql change — the app role's ALTER DEFAULT PRIVILEGES
on clearinghouse_core auto-grants DML on the new table, and the #54 REVOKE is scoped to
fetch_events/raw_payloads/citations. Classified **mutable** in the #54 append-only guard
(``scripts/tests/test_grants_append_only.py``): a re-audit UPDATEs its row in place.

Revision ID: 02bb603b7702
Revises: 1347c49fd6bf
Create Date: 2026-08-09
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

import clearinghouse_core.db.ulid

# revision identifiers, used by Alembic.
revision: str = "02bb603b7702"
down_revision: str | Sequence[str] | None = "1347c49fd6bf"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

CHCORE = "clearinghouse_core"


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "source_coverage",
        sa.Column("id", clearinghouse_core.db.ulid.ULID(), nullable=False),
        sa.Column("source_id", clearinghouse_core.db.ulid.ULID(), nullable=False),
        sa.Column("dimension", sa.String(length=64), nullable=False),
        sa.Column("range_start", sa.String(length=32), nullable=False),
        sa.Column("range_end", sa.String(length=32), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("audited_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("evidence_citation_id", clearinghouse_core.db.ulid.ULID(), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
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
        sa.CheckConstraint(
            "status IN ('verified', 'assumed', 'absent')",
            name="ck_source_coverage_status",
        ),
        sa.ForeignKeyConstraint(
            ["evidence_citation_id"],
            [f"{CHCORE}.citations.id"],
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(["source_id"], [f"{CHCORE}.sources.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "source_id", "dimension", "range_start", name="uq_source_coverage_span"
        ),
        schema=CHCORE,
    )
    op.create_index(
        op.f(f"ix_{CHCORE}_source_coverage_source_id"),
        "source_coverage",
        ["source_id"],
        unique=False,
        schema=CHCORE,
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(
        op.f(f"ix_{CHCORE}_source_coverage_source_id"),
        table_name="source_coverage",
        schema=CHCORE,
    )
    op.drop_table("source_coverage", schema=CHCORE)
