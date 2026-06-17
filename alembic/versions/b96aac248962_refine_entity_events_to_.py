"""refine entity_events to ObservationEventItem shape

Refines ``canonical.entity_events`` to mirror Power Map's ``ObservationEventItem``
(power-map#170). Replaces the single ``date`` column with granular, individually
nullable partial-date components (``event_year`` … ``event_second``), and the
bare ``event_type`` string with ``event_type_slug`` XOR ``event_type_id``. Adds
``event_place_text``, a constrained ``visibility`` (public | legal_only | hidden),
and an optional polymorphic ``linked_entity`` (kind + id, set together).

The table is currently unused (entity events are out of the MVP increment), so
this refinement is non-breaking. CHECK constraints are hand-added — autogenerate
does not emit them. See
``docs/specs/2026-06-02-power-map-sync-sidecar-design.md`` (Changelog + §2).

Revision ID: b96aac248962
Revises: d2e3f4a5b6c7
Create Date: 2026-06-17 00:30:35.220466

"""
from collections.abc import Sequence
from typing import Union

import clearinghouse_core.db.ulid
import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b96aac248962"
down_revision: Union[str, Sequence[str], None] = "d2e3f4a5b6c7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

CANON = "canonical"


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        "entity_events",
        sa.Column("event_type_slug", sa.String(length=64), nullable=True),
        schema=CANON,
    )
    op.add_column(
        "entity_events",
        sa.Column("event_type_id", clearinghouse_core.db.ulid.ULID(), nullable=True),
        schema=CANON,
    )
    op.add_column(
        "entity_events", sa.Column("event_year", sa.Integer(), nullable=True), schema=CANON
    )
    op.add_column(
        "entity_events", sa.Column("event_month", sa.Integer(), nullable=True), schema=CANON
    )
    op.add_column(
        "entity_events", sa.Column("event_day", sa.Integer(), nullable=True), schema=CANON
    )
    op.add_column(
        "entity_events", sa.Column("event_hour", sa.Integer(), nullable=True), schema=CANON
    )
    op.add_column(
        "entity_events", sa.Column("event_minute", sa.Integer(), nullable=True), schema=CANON
    )
    op.add_column(
        "entity_events", sa.Column("event_second", sa.Integer(), nullable=True), schema=CANON
    )
    op.add_column(
        "entity_events", sa.Column("event_place_text", sa.Text(), nullable=True), schema=CANON
    )
    op.add_column(
        "entity_events",
        sa.Column(
            "visibility",
            sa.String(length=16),
            nullable=False,
            server_default="public",
        ),
        schema=CANON,
    )
    op.add_column(
        "entity_events",
        sa.Column("linked_entity_kind", sa.String(length=16), nullable=True),
        schema=CANON,
    )
    op.add_column(
        "entity_events",
        sa.Column("linked_entity_id", clearinghouse_core.db.ulid.ULID(), nullable=True),
        schema=CANON,
    )

    # Drop the old single-date + bare-type columns the new shape supersedes.
    op.drop_column("entity_events", "date", schema=CANON)
    op.drop_column("entity_events", "event_type", schema=CANON)

    # CHECK constraints (autogenerate omits these).
    op.create_check_constraint(
        "ck_entity_events_event_type_xor",
        "entity_events",
        "(event_type_slug IS NOT NULL) <> (event_type_id IS NOT NULL)",
        schema=CANON,
    )
    op.create_check_constraint(
        "ck_entity_events_visibility",
        "entity_events",
        "visibility IN ('public', 'legal_only', 'hidden')",
        schema=CANON,
    )
    op.create_check_constraint(
        "ck_entity_events_linked_entity_kind",
        "entity_events",
        "linked_entity_kind IS NULL OR linked_entity_kind IN ('person', 'organization')",
        schema=CANON,
    )
    op.create_check_constraint(
        "ck_entity_events_linked_entity_together",
        "entity_events",
        "(linked_entity_kind IS NULL) = (linked_entity_id IS NULL)",
        schema=CANON,
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_constraint(
        "ck_entity_events_linked_entity_together",
        "entity_events",
        schema=CANON,
        type_="check",
    )
    op.drop_constraint(
        "ck_entity_events_linked_entity_kind",
        "entity_events",
        schema=CANON,
        type_="check",
    )
    op.drop_constraint(
        "ck_entity_events_visibility", "entity_events", schema=CANON, type_="check"
    )
    op.drop_constraint(
        "ck_entity_events_event_type_xor", "entity_events", schema=CANON, type_="check"
    )

    # Restore the pre-refinement columns.
    op.add_column(
        "entity_events",
        sa.Column("event_type", sa.VARCHAR(length=32), autoincrement=False, nullable=False),
        schema=CANON,
    )
    op.add_column(
        "entity_events",
        sa.Column("date", sa.DATE(), autoincrement=False, nullable=True),
        schema=CANON,
    )

    op.drop_column("entity_events", "linked_entity_id", schema=CANON)
    op.drop_column("entity_events", "linked_entity_kind", schema=CANON)
    op.drop_column("entity_events", "visibility", schema=CANON)
    op.drop_column("entity_events", "event_place_text", schema=CANON)
    op.drop_column("entity_events", "event_second", schema=CANON)
    op.drop_column("entity_events", "event_minute", schema=CANON)
    op.drop_column("entity_events", "event_hour", schema=CANON)
    op.drop_column("entity_events", "event_day", schema=CANON)
    op.drop_column("entity_events", "event_month", schema=CANON)
    op.drop_column("entity_events", "event_year", schema=CANON)
    op.drop_column("entity_events", "event_type_id", schema=CANON)
    op.drop_column("entity_events", "event_type_slug", schema=CANON)
