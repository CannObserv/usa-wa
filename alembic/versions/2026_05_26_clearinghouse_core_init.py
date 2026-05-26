"""clearinghouse_core init — schema, provenance tables, seed Jurisdiction(usa-wa).

Revision ID: 2026_05_26_clearinghouse_core_init
Revises:
Create Date: 2026-05-26
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from ulid import ULID as _ULID

from clearinghouse_core.db.ulid import ULID

revision: str = "20260526_chcore_init"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "clearinghouse_core"


def upgrade() -> None:
    """Create the clearinghouse_core schema, provenance tables, and seed usa-wa."""
    op.execute(f'CREATE SCHEMA IF NOT EXISTS "{SCHEMA}"')

    op.create_table(
        "jurisdictions",
        sa.Column("id", ULID(), nullable=False),
        sa.Column("slug", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("level", sa.String(length=16), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("slug", name="uq_jurisdictions_slug"),
        schema=SCHEMA,
    )

    op.create_table(
        "sources",
        sa.Column("id", ULID(), nullable=False),
        sa.Column("jurisdiction_id", ULID(), nullable=False),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("slug", sa.String(length=64), nullable=False),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("base_url", sa.String(length=512), nullable=True),
        sa.Column("reliability", sa.Float(), nullable=False),
        sa.Column("cache_ttl_days", sa.Integer(), nullable=False),
        sa.Column("config", sa.JSON(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.ForeignKeyConstraint(
            ["jurisdiction_id"], [f"{SCHEMA}.jurisdictions.id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("slug", name="uq_sources_slug"),
        schema=SCHEMA,
    )

    op.create_table(
        "fetch_events",
        sa.Column("id", ULID(), nullable=False),
        sa.Column("source_id", ULID(), nullable=False),
        sa.Column("resource_id", sa.String(length=256), nullable=False),
        sa.Column("resource_version_key", sa.String(length=256), nullable=True),
        sa.Column("url", sa.String(length=2048), nullable=False),
        sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("http_status", sa.Integer(), nullable=True),
        sa.Column("content_hash", sa.LargeBinary(length=32), nullable=True),
        sa.Column("etag", sa.String(length=256), nullable=True),
        sa.Column("last_modified", sa.String(length=64), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.ForeignKeyConstraint(["source_id"], [f"{SCHEMA}.sources.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        schema=SCHEMA,
    )
    op.create_index(
        op.f("ix_clearinghouse_core_fetch_events_source_id"),
        "fetch_events",
        ["source_id"],
        unique=False,
        schema=SCHEMA,
    )
    op.create_index(
        op.f("ix_clearinghouse_core_fetch_events_resource_id"),
        "fetch_events",
        ["resource_id"],
        unique=False,
        schema=SCHEMA,
    )

    op.create_table(
        "raw_payloads",
        sa.Column("id", ULID(), nullable=False),
        sa.Column("fetch_event_id", ULID(), nullable=False),
        sa.Column("content_type", sa.String(length=128), nullable=False),
        sa.Column("body", sa.LargeBinary(), nullable=False),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.ForeignKeyConstraint(
            ["fetch_event_id"], [f"{SCHEMA}.fetch_events.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("fetch_event_id", name="uq_raw_payloads_fetch_event"),
        schema=SCHEMA,
    )

    op.create_table(
        "citations",
        sa.Column("id", ULID(), nullable=False),
        sa.Column("entity_type", sa.String(length=64), nullable=False),
        sa.Column("entity_id", ULID(), nullable=False),
        sa.Column("fetch_event_id", ULID(), nullable=False),
        sa.Column("field_path", sa.String(length=128), nullable=True),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("asserted_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.ForeignKeyConstraint(
            ["fetch_event_id"], [f"{SCHEMA}.fetch_events.id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id"),
        schema=SCHEMA,
    )
    op.create_index(
        op.f("ix_clearinghouse_core_citations_entity_type"),
        "citations",
        ["entity_type"],
        unique=False,
        schema=SCHEMA,
    )
    op.create_index(
        op.f("ix_clearinghouse_core_citations_entity_id"),
        "citations",
        ["entity_id"],
        unique=False,
        schema=SCHEMA,
    )

    # --- seed: the WA jurisdiction (idempotent ON CONFLICT) ---
    seed_id = _ULID().to_uuid()
    op.execute(
        sa.text(
            f"""
            INSERT INTO "{SCHEMA}".jurisdictions (id, slug, name, level)
            VALUES (:id, :slug, :name, :level)
            ON CONFLICT (slug) DO NOTHING
            """
        ).bindparams(id=seed_id, slug="usa-wa", name="Washington State", level="state")
    )


def downgrade() -> None:
    """Drop everything in reverse, then the schema itself."""
    op.drop_index(
        op.f("ix_clearinghouse_core_citations_entity_id"),
        table_name="citations",
        schema=SCHEMA,
    )
    op.drop_index(
        op.f("ix_clearinghouse_core_citations_entity_type"),
        table_name="citations",
        schema=SCHEMA,
    )
    op.drop_table("citations", schema=SCHEMA)
    op.drop_table("raw_payloads", schema=SCHEMA)
    op.drop_index(
        op.f("ix_clearinghouse_core_fetch_events_resource_id"),
        table_name="fetch_events",
        schema=SCHEMA,
    )
    op.drop_index(
        op.f("ix_clearinghouse_core_fetch_events_source_id"),
        table_name="fetch_events",
        schema=SCHEMA,
    )
    op.drop_table("fetch_events", schema=SCHEMA)
    op.drop_table("sources", schema=SCHEMA)
    op.drop_table("jurisdictions", schema=SCHEMA)
    op.execute(f'DROP SCHEMA IF EXISTS "{SCHEMA}" CASCADE')
