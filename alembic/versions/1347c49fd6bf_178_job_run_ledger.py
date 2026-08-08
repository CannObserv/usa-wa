"""add clearinghouse_core.job_runs — the job-run ledger (#178)

One row per execution of an operational job: when it started, when it finished, how it
ended, and the counters that justify that verdict. Nothing recorded this before, so the
failure mode that actually bites — a job that exits 0 having silently done nothing —
was invisible: alerting is exit-code driven, and the one signal that names the case
(``results_harvest_total_outage``) is a WARNING with no consumer.

``outcome`` carries three terminal values, CHECK-constrained so the vocabulary can't
drift per-job: ``ok`` | ``degraded`` | ``failed``. ``degraded`` is the new one — the run
completed but its work did not land — and the harness
(:mod:`clearinghouse_core.job`) maps it to its own non-zero exit code so systemd's
``OnFailure=`` fires on it. It is nullable, along with ``finished_at``, because the row
is written *before* the work: a row still NULL at both is a job that never reported
back (killed, OOM, hung past ``TimeoutStartSec=``), a state a write-at-the-end ledger
cannot represent.

The ``(job_slug, started_at)`` index serves the ledger's two reads: this job's recent
history, and "when did each job last finish ok?" (staleness).

Additive: no existing table changes and nothing writes here until a job adopts the
harness. No grants.sql change — the app role's ALTER DEFAULT PRIVILEGES on
clearinghouse_core auto-grants DML on this new table, and the #54 REVOKE is scoped to
fetch_events/raw_payloads/citations only.

Revision ID: 1347c49fd6bf
Revises: 890c046c6e58
Create Date: 2026-08-07
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

import clearinghouse_core.db.ulid

revision: str = "1347c49fd6bf"
down_revision: str | Sequence[str] | None = "890c046c6e58"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

CHCORE = "clearinghouse_core"


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "job_runs",
        sa.Column("id", clearinghouse_core.db.ulid.ULID(), nullable=False),
        sa.Column("job_slug", sa.String(length=128), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("outcome", sa.String(length=16), nullable=True),
        sa.Column("counters", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("git_sha", sa.String(length=40), nullable=True),
        sa.Column("host", sa.String(length=255), nullable=True),
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
            "outcome IS NULL OR outcome IN ('ok', 'degraded', 'failed')",
            name="ck_job_runs_outcome",
        ),
        sa.PrimaryKeyConstraint("id"),
        schema=CHCORE,
    )
    op.create_index(
        "ix_job_runs_job_slug_started_at",
        "job_runs",
        ["job_slug", "started_at"],
        unique=False,
        schema=CHCORE,
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index("ix_job_runs_job_slug_started_at", table_name="job_runs", schema=CHCORE)
    op.drop_table("job_runs", schema=CHCORE)
