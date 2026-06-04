"""powermap sync sidecar: anchors, entity_events, sync schema

Standardizes the canonical PM anchors to ``pm_<entity>_id`` (renaming the two
legacy ``powermap_*`` columns, adding role/assignment anchors), creates the
``canonical.entity_events`` mirror table, and introduces the ``sync`` schema
with the durable outbox + sync-state tables that back the sidecar engine.

Revision ID: bd90e2ab379a
Revises: 20260603_jurisdictional_ia
Create Date: 2026-06-04
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

import clearinghouse_core.db.ulid

revision: str = "bd90e2ab379a"
down_revision: str | Sequence[str] | None = "20260603_jurisdictional_ia"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

CANON = "canonical"
SYNC = "sync"


def upgrade() -> None:
    """Upgrade schema."""
    # --- sync schema: durable outbox + cursor state ---------------------------
    op.execute(f'CREATE SCHEMA IF NOT EXISTS "{SYNC}"')

    op.create_table(
        "powermap_outbox",
        sa.Column("id", clearinghouse_core.db.ulid.ULID(), nullable=False),
        sa.Column("entity_type", sa.String(length=64), nullable=False),
        sa.Column("local_id", clearinghouse_core.db.ulid.ULID(), nullable=False),
        sa.Column("op", sa.String(length=16), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column(
            "next_attempt_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("last_disposition", sa.String(length=32), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.CheckConstraint("op IN ('CREATE', 'UPDATE')", name="ck_powermap_outbox_op"),
        sa.CheckConstraint(
            "status IN ('PENDING', 'DELIVERED', 'REJECTED')", name="ck_powermap_outbox_status"
        ),
        sa.PrimaryKeyConstraint("id"),
        schema=SYNC,
    )
    op.create_index(
        "ix_powermap_outbox_due", "powermap_outbox", ["status", "next_attempt_at"], schema=SYNC
    )
    op.create_index(
        "uq_powermap_outbox_open",
        "powermap_outbox",
        ["entity_type", "local_id"],
        unique=True,
        schema=SYNC,
        postgresql_where=sa.text("status = 'PENDING'"),
    )

    op.create_table(
        "powermap_sync_state",
        sa.Column("id", clearinghouse_core.db.ulid.ULID(), nullable=False),
        sa.Column("stream", sa.String(length=64), nullable=False),
        sa.Column("cursor", sa.String(length=256), nullable=True),
        sa.Column("last_reconcile_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("stream", name="uq_powermap_sync_state_stream"),
        schema=SYNC,
    )

    # --- canonical.entity_events mirror ---------------------------------------
    op.create_table(
        "entity_events",
        sa.Column("id", clearinghouse_core.db.ulid.ULID(), nullable=False),
        sa.Column("jurisdiction_id", clearinghouse_core.db.ulid.ULID(), nullable=False),
        sa.Column("source", sa.String(length=64), nullable=False),
        sa.Column("source_id", sa.String(length=128), nullable=False),
        sa.Column("entity_kind", sa.String(length=16), nullable=False),
        sa.Column("entity_id", clearinghouse_core.db.ulid.ULID(), nullable=False),
        sa.Column("event_type", sa.String(length=32), nullable=False),
        sa.Column("date", sa.Date(), nullable=True),
        sa.Column("pm_entity_event_id", clearinghouse_core.db.ulid.ULID(), nullable=True),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.CheckConstraint(
            "entity_kind IN ('person', 'organization')", name="ck_entity_events_kind"
        ),
        sa.ForeignKeyConstraint(
            ["jurisdiction_id"], ["clearinghouse_core.jurisdictions.id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "jurisdiction_id", "source", "source_id", name="uq_entity_events_natural_key"
        ),
        schema=CANON,
    )
    op.create_index(
        op.f("ix_canonical_entity_events_entity_id"),
        "entity_events",
        ["entity_id"],
        schema=CANON,
    )
    op.create_index(
        op.f("ix_canonical_entity_events_jurisdiction_id"),
        "entity_events",
        ["jurisdiction_id"],
        schema=CANON,
    )
    op.create_index(
        op.f("ix_canonical_entity_events_pm_entity_event_id"),
        "entity_events",
        ["pm_entity_event_id"],
        schema=CANON,
    )

    # --- new anchors: roles + assignments -------------------------------------
    op.add_column(
        "assignments",
        sa.Column("pm_assignment_id", clearinghouse_core.db.ulid.ULID(), nullable=True),
        schema=CANON,
    )
    op.create_index(
        op.f("ix_canonical_assignments_pm_assignment_id"),
        "assignments",
        ["pm_assignment_id"],
        schema=CANON,
    )
    op.add_column(
        "roles",
        sa.Column("pm_role_id", clearinghouse_core.db.ulid.ULID(), nullable=True),
        schema=CANON,
    )
    op.create_index(
        op.f("ix_canonical_roles_pm_role_id"), "roles", ["pm_role_id"], schema=CANON
    )

    # --- rename legacy anchors: powermap_* -> pm_* (preserve column data) ------
    op.alter_column(
        "organizations", "powermap_organization_id", new_column_name="pm_organization_id",
        schema=CANON,
    )
    op.create_index(
        op.f("ix_canonical_organizations_pm_organization_id"),
        "organizations",
        ["pm_organization_id"],
        schema=CANON,
    )
    op.alter_column(
        "persons", "powermap_person_id", new_column_name="pm_person_id", schema=CANON
    )
    op.create_index(
        op.f("ix_canonical_persons_pm_person_id"), "persons", ["pm_person_id"], schema=CANON
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(op.f("ix_canonical_persons_pm_person_id"), table_name="persons", schema=CANON)
    op.alter_column(
        "persons", "pm_person_id", new_column_name="powermap_person_id", schema=CANON
    )
    op.drop_index(
        op.f("ix_canonical_organizations_pm_organization_id"),
        table_name="organizations",
        schema=CANON,
    )
    op.alter_column(
        "organizations", "pm_organization_id", new_column_name="powermap_organization_id",
        schema=CANON,
    )

    op.drop_index(op.f("ix_canonical_roles_pm_role_id"), table_name="roles", schema=CANON)
    op.drop_column("roles", "pm_role_id", schema=CANON)
    op.drop_index(
        op.f("ix_canonical_assignments_pm_assignment_id"), table_name="assignments", schema=CANON
    )
    op.drop_column("assignments", "pm_assignment_id", schema=CANON)

    op.drop_index(
        op.f("ix_canonical_entity_events_pm_entity_event_id"),
        table_name="entity_events",
        schema=CANON,
    )
    op.drop_index(
        op.f("ix_canonical_entity_events_jurisdiction_id"),
        table_name="entity_events",
        schema=CANON,
    )
    op.drop_index(
        op.f("ix_canonical_entity_events_entity_id"), table_name="entity_events", schema=CANON
    )
    op.drop_table("entity_events", schema=CANON)

    op.drop_table("powermap_sync_state", schema=SYNC)
    op.drop_index(
        "uq_powermap_outbox_open",
        table_name="powermap_outbox",
        schema=SYNC,
        postgresql_where=sa.text("status = 'PENDING'"),
    )
    op.drop_index("ix_powermap_outbox_due", table_name="powermap_outbox", schema=SYNC)
    op.drop_table("powermap_outbox", schema=SYNC)
    op.execute(f'DROP SCHEMA IF EXISTS "{SYNC}" CASCADE')
