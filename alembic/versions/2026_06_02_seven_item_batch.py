"""v1.3 seven-item review batch — hybrid IA review #1.

Bundles the seven actionable schema changes from the 2026-05-30 hybrid IA
review. See individual sections of the hybrid IA spec for design rationale.

1. **Document identifier UNIQUE relaxation.** The third constraint on
   ``clearinghouse_core.document_identifiers`` is rewidened to include
   ``entity_type`` so the same identifier (e.g., Code Reviser
   ``H-0734.1/25``) can legitimately attach to BOTH an Amendment row AND
   the resulting BillVersion row when a substitute/striking amendment
   becomes a new bill text.

2. **Drop ``LegislativeSession.adjourned_sine_die_at``.** Functionally
   redundant with ``end_date`` for the WA use case; precise timestamps
   can be added back if a query needs them.

3. **``canonical.bill_types`` lookup table.** ``Bill.bill_type`` +
   ``Bill.classification`` text columns are replaced by a FK to
   ``canonical.bill_types``. The lookup carries code, display_name, AND
   the OCD-aligned classification — so the two fields stay in lockstep
   without per-row drift.

4. **Drop ``BillAction.chamber``.** Derivable from
   ``acting_organization_id`` (the chamber Org for chamber-level actions,
   or an ancestor of acting_organization_id when a committee acts).

5. **``Amendment.bill_id`` → ``Amendment.bill_version_id``.** Every
   amendment is against a specific version. ``Amendment.amendment_text``
   is dropped (substitute / striking amendments use their target
   BillVersion's text). ``BillVersion.amendment_id`` back-link is also
   dropped — relinking is forward-only now.

6. **``bill_titles.chamber`` text → Org FK.** Chambers are first-class
   Organizations; chamber refs are FKs throughout.

7. **``canonical.bill_relationship_types`` lookup table** with a
   ``symmetric`` column. ``bill_relationships.relationship_type`` text
   is replaced by a FK to the lookup. The lookup encodes the symmetric
   property once per type rather than relying on per-relationship
   documentation.

No data migration concerns — usa-wa hasn't started ingesting yet.

Revision ID: 20260602_seven_item_batch
Revises: 20260601_bill_class_sponsored_at
Create Date: 2026-06-02
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

import clearinghouse_core.db.ulid

revision: str = "20260602_seven_item_batch"
down_revision: str | Sequence[str] | None = "20260601_bill_class_sponsored_at"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

CANONICAL = "canonical"
CHCORE = "clearinghouse_core"


def upgrade() -> None:
    # --- #10 bill_relationship_types lookup ---
    op.create_table(
        "bill_relationship_types",
        sa.Column("id", clearinghouse_core.db.ulid.ULID(), nullable=False),
        sa.Column("jurisdiction_id", sa.String(length=32), nullable=False),
        sa.Column("code", sa.String(length=32), nullable=False),
        sa.Column("display_name", sa.String(length=64), nullable=False),
        sa.Column("symmetric", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("description", sa.Text(), nullable=True),
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
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "jurisdiction_id",
            "code",
            name="uq_bill_relationship_types_jurisdiction_code",
        ),
        schema=CANONICAL,
    )
    op.create_index(
        op.f("ix_canonical_bill_relationship_types_jurisdiction_id"),
        "bill_relationship_types",
        ["jurisdiction_id"],
        unique=False,
        schema=CANONICAL,
    )

    # --- #5 bill_types lookup ---
    op.create_table(
        "bill_types",
        sa.Column("id", clearinghouse_core.db.ulid.ULID(), nullable=False),
        sa.Column("jurisdiction_id", sa.String(length=32), nullable=False),
        sa.Column("code", sa.String(length=32), nullable=False),
        sa.Column("display_name", sa.String(length=128), nullable=False),
        sa.Column("classification", sa.String(length=32), nullable=True),
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
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "jurisdiction_id", "code", name="uq_bill_types_jurisdiction_code"
        ),
        schema=CANONICAL,
    )
    op.create_index(
        op.f("ix_canonical_bill_types_jurisdiction_id"),
        "bill_types",
        ["jurisdiction_id"],
        unique=False,
        schema=CANONICAL,
    )

    # --- #5 Bill.bill_type/classification → bill_type_id FK ---
    op.add_column(
        "bills",
        sa.Column("bill_type_id", clearinghouse_core.db.ulid.ULID(), nullable=True),
        schema=CANONICAL,
    )
    op.create_index(
        op.f("ix_canonical_bills_bill_type_id"),
        "bills",
        ["bill_type_id"],
        unique=False,
        schema=CANONICAL,
    )
    op.create_foreign_key(
        "fk_bills_bill_type_id",
        "bills",
        "bill_types",
        ["bill_type_id"],
        ["id"],
        source_schema=CANONICAL,
        referent_schema=CANONICAL,
        ondelete="RESTRICT",
    )
    op.drop_column("bills", "bill_type", schema=CANONICAL)
    op.drop_column("bills", "classification", schema=CANONICAL)

    # --- #3 Drop LegislativeSession.adjourned_sine_die_at ---
    op.drop_column(
        "legislative_sessions", "adjourned_sine_die_at", schema=CANONICAL
    )

    # --- #6 Drop BillAction.chamber ---
    op.drop_column("bill_actions", "chamber", schema=CANONICAL)

    # --- #9 bill_titles.chamber → Org FK ---
    op.add_column(
        "bill_titles",
        sa.Column("chamber_id", clearinghouse_core.db.ulid.ULID(), nullable=True),
        schema=CANONICAL,
    )
    op.create_index(
        op.f("ix_canonical_bill_titles_chamber_id"),
        "bill_titles",
        ["chamber_id"],
        unique=False,
        schema=CANONICAL,
    )
    op.create_foreign_key(
        "fk_bill_titles_chamber_id",
        "bill_titles",
        "organizations",
        ["chamber_id"],
        ["id"],
        source_schema=CANONICAL,
        referent_schema=CANONICAL,
        ondelete="RESTRICT",
    )
    op.drop_column("bill_titles", "chamber", schema=CANONICAL)

    # --- #10 bill_relationships.relationship_type → relationship_type_id FK ---
    op.add_column(
        "bill_relationships",
        sa.Column(
            "relationship_type_id",
            clearinghouse_core.db.ulid.ULID(),
            nullable=False,
        ),
        schema=CANONICAL,
    )
    op.drop_constraint(
        "uq_bill_relationships_pair_type",
        "bill_relationships",
        schema=CANONICAL,
        type_="unique",
    )
    op.create_unique_constraint(
        "uq_bill_relationships_pair_type",
        "bill_relationships",
        ["from_bill_id", "to_bill_id", "relationship_type_id"],
        schema=CANONICAL,
    )
    op.create_index(
        op.f("ix_canonical_bill_relationships_relationship_type_id"),
        "bill_relationships",
        ["relationship_type_id"],
        unique=False,
        schema=CANONICAL,
    )
    op.create_foreign_key(
        "fk_bill_relationships_relationship_type_id",
        "bill_relationships",
        "bill_relationship_types",
        ["relationship_type_id"],
        ["id"],
        source_schema=CANONICAL,
        referent_schema=CANONICAL,
        ondelete="RESTRICT",
    )
    op.drop_column("bill_relationships", "relationship_type", schema=CANONICAL)

    # --- #8 BillVersion.amendment_id drop (back-link no longer needed) ---
    op.drop_index(
        op.f("ix_canonical_bill_versions_amendment_id"),
        table_name="bill_versions",
        schema=CANONICAL,
    )
    op.drop_constraint(
        "fk_bill_versions_amendment_id",
        "bill_versions",
        schema=CANONICAL,
        type_="foreignkey",
    )
    op.drop_column("bill_versions", "amendment_id", schema=CANONICAL)

    # --- #8 Amendment.bill_id → bill_version_id; drop amendment_text ---
    op.add_column(
        "amendments",
        sa.Column(
            "bill_version_id", clearinghouse_core.db.ulid.ULID(), nullable=False
        ),
        schema=CANONICAL,
    )
    op.drop_index(
        op.f("ix_canonical_amendments_bill_id"),
        table_name="amendments",
        schema=CANONICAL,
    )
    op.create_index(
        op.f("ix_canonical_amendments_bill_version_id"),
        "amendments",
        ["bill_version_id"],
        unique=False,
        schema=CANONICAL,
    )
    op.drop_constraint(
        op.f("amendments_bill_id_fkey"),
        "amendments",
        schema=CANONICAL,
        type_="foreignkey",
    )
    op.create_foreign_key(
        "fk_amendments_bill_version_id",
        "amendments",
        "bill_versions",
        ["bill_version_id"],
        ["id"],
        source_schema=CANONICAL,
        referent_schema=CANONICAL,
        ondelete="CASCADE",
    )
    op.drop_column("amendments", "bill_id", schema=CANONICAL)
    op.drop_column("amendments", "amendment_text", schema=CANONICAL)

    # --- #1 Document identifier UNIQUE relaxation ---
    op.drop_constraint(
        "uq_document_identifiers_jurisdiction_scheme_value",
        "document_identifiers",
        schema=CHCORE,
        type_="unique",
    )
    op.create_unique_constraint(
        "uq_document_identifiers_jurisdiction_entity_scheme_value",
        "document_identifiers",
        ["jurisdiction_id", "entity_type", "scheme", "value"],
        schema=CHCORE,
    )


def downgrade() -> None:
    op.drop_constraint(
        "uq_document_identifiers_jurisdiction_entity_scheme_value",
        "document_identifiers",
        schema=CHCORE,
        type_="unique",
    )
    op.create_unique_constraint(
        "uq_document_identifiers_jurisdiction_scheme_value",
        "document_identifiers",
        ["jurisdiction_id", "scheme", "value"],
        schema=CHCORE,
    )

    op.add_column(
        "amendments",
        sa.Column("amendment_text", sa.Text(), nullable=True),
        schema=CANONICAL,
    )
    op.add_column(
        "amendments",
        sa.Column("bill_id", clearinghouse_core.db.ulid.ULID(), nullable=False),
        schema=CANONICAL,
    )
    op.drop_constraint(
        "fk_amendments_bill_version_id",
        "amendments",
        schema=CANONICAL,
        type_="foreignkey",
    )
    op.create_foreign_key(
        "amendments_bill_id_fkey",
        "amendments",
        "bills",
        ["bill_id"],
        ["id"],
        source_schema=CANONICAL,
        referent_schema=CANONICAL,
        ondelete="CASCADE",
    )
    op.drop_index(
        op.f("ix_canonical_amendments_bill_version_id"),
        table_name="amendments",
        schema=CANONICAL,
    )
    op.create_index(
        op.f("ix_canonical_amendments_bill_id"),
        "amendments",
        ["bill_id"],
        unique=False,
        schema=CANONICAL,
    )
    op.drop_column("amendments", "bill_version_id", schema=CANONICAL)

    op.add_column(
        "bill_versions",
        sa.Column("amendment_id", clearinghouse_core.db.ulid.ULID(), nullable=True),
        schema=CANONICAL,
    )
    op.create_foreign_key(
        "fk_bill_versions_amendment_id",
        "bill_versions",
        "amendments",
        ["amendment_id"],
        ["id"],
        source_schema=CANONICAL,
        referent_schema=CANONICAL,
        ondelete="SET NULL",
    )
    op.create_index(
        op.f("ix_canonical_bill_versions_amendment_id"),
        "bill_versions",
        ["amendment_id"],
        unique=False,
        schema=CANONICAL,
    )

    op.add_column(
        "bill_relationships",
        sa.Column("relationship_type", sa.String(length=32), nullable=False),
        schema=CANONICAL,
    )
    op.drop_constraint(
        "fk_bill_relationships_relationship_type_id",
        "bill_relationships",
        schema=CANONICAL,
        type_="foreignkey",
    )
    op.drop_index(
        op.f("ix_canonical_bill_relationships_relationship_type_id"),
        table_name="bill_relationships",
        schema=CANONICAL,
    )
    op.drop_constraint(
        "uq_bill_relationships_pair_type",
        "bill_relationships",
        schema=CANONICAL,
        type_="unique",
    )
    op.create_unique_constraint(
        "uq_bill_relationships_pair_type",
        "bill_relationships",
        ["from_bill_id", "to_bill_id", "relationship_type"],
        schema=CANONICAL,
    )
    op.drop_column("bill_relationships", "relationship_type_id", schema=CANONICAL)

    op.add_column(
        "bill_titles",
        sa.Column("chamber", sa.String(length=16), nullable=True),
        schema=CANONICAL,
    )
    op.drop_constraint(
        "fk_bill_titles_chamber_id",
        "bill_titles",
        schema=CANONICAL,
        type_="foreignkey",
    )
    op.drop_index(
        op.f("ix_canonical_bill_titles_chamber_id"),
        table_name="bill_titles",
        schema=CANONICAL,
    )
    op.drop_column("bill_titles", "chamber_id", schema=CANONICAL)

    op.add_column(
        "bill_actions",
        sa.Column("chamber", sa.String(length=16), nullable=True),
        schema=CANONICAL,
    )

    op.add_column(
        "legislative_sessions",
        sa.Column("adjourned_sine_die_at", sa.DateTime(timezone=True), nullable=True),
        schema=CANONICAL,
    )

    op.add_column(
        "bills",
        sa.Column("classification", sa.String(length=32), nullable=True),
        schema=CANONICAL,
    )
    op.add_column(
        "bills",
        sa.Column("bill_type", sa.String(length=32), nullable=True),
        schema=CANONICAL,
    )
    op.drop_constraint(
        "fk_bills_bill_type_id", "bills", schema=CANONICAL, type_="foreignkey"
    )
    op.drop_index(
        op.f("ix_canonical_bills_bill_type_id"),
        table_name="bills",
        schema=CANONICAL,
    )
    op.drop_column("bills", "bill_type_id", schema=CANONICAL)

    op.drop_index(
        op.f("ix_canonical_bill_types_jurisdiction_id"),
        table_name="bill_types",
        schema=CANONICAL,
    )
    op.drop_table("bill_types", schema=CANONICAL)

    op.drop_index(
        op.f("ix_canonical_bill_relationship_types_jurisdiction_id"),
        table_name="bill_relationship_types",
        schema=CANONICAL,
    )
    op.drop_table("bill_relationship_types", schema=CANONICAL)
