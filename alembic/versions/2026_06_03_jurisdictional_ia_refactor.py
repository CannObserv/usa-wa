"""Jurisdictional IA refactor — cache tables + schema-wide jurisdiction_id text→ULID FK.

Step 4 of the Jurisdictional IA plan
(:file:`docs/plans/2026-05-31-jurisdictional-ia-implementation.md`). Lands:

1. New cache tables in ``clearinghouse_core``:
   - ``jurisdiction_types`` (16-row lookup mirroring PM)
   - ``jurisdiction_relationship_types`` (11-row lookup; PM dropped
     ``exercises_concurrent_jurisdiction`` from MVP)
   - ``jurisdiction_relationships`` (bitemporal junction with partial unique
     index closing PostgreSQL's NULL-distinct UNIQUE gap on the natural key)

2. ``clearinghouse_core.jurisdictions`` extended in place:
   - drop the old ``level`` column + ``JurisdictionLevel`` StrEnum
   - widen ``slug`` (64→128) and ``name`` (varchar(128)→text)
   - add ``pm_jurisdiction_id``, ``type_id`` FK, bitemporal
     ``valid_from`` / ``valid_until`` / ``recorded_at`` / ``superseded_at``

3. Pre-seed from ``packages/usa-wa-adapter-legislature/.../initial_jurisdictions.json``:
   - 101 jurisdictions (1 country + 1 state + 49 LDs + 10 CDs + 39 counties + Seattle)
   - 101 relationships (state→country, 99 sub-state containments, Seattle→King
     example)
   - Idempotent: ``INSERT ... ON CONFLICT (slug) DO NOTHING``

4. Schema-wide ``jurisdiction_id`` text(32) → ULID FK refactor across the 34
   canonical tables that carry it. usa-wa hasn't started ingestion (P0.5 has
   not begun) so any existing row is the literal slug ``'usa-wa'`` and resolves
   to the bootstrap-seeded ULID via inline ``USING`` clause.

5. ``Role.district`` (text(32) label) dropped — the new
   ``Role.jurisdiction_id`` FK carries the district reference directly (e.g.,
   ``jurisdiction_id`` → ``usa-wa-ld-21`` row). The
   ``uq_roles_org_name_district`` UNIQUE collapses to ``uq_roles_org_name``.

Single transaction; alembic wraps the upgrade in a BEGIN/COMMIT automatically.

Downgrade restores the prior text(32) shape but **does not back-fill
slug-shaped strings** beyond ``'usa-wa'``: downgrade is for emergency rollback
of a freshly-applied migration, not for permanent reversion.

Revision ID: 20260603_jurisdictional_ia
Revises: 20260602_seven_item_batch
Create Date: 2026-06-03
"""

import json
from collections.abc import Sequence
from pathlib import Path

import sqlalchemy as sa
from alembic import op
from ulid import ULID as _ULID

import clearinghouse_core.db.ulid

revision: str = "20260603_jurisdictional_ia"
down_revision: str | Sequence[str] | None = "20260602_seven_item_batch"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

CC = "clearinghouse_core"
CANONICAL = "canonical"

JSON_SEED_PATH = (
    Path(__file__).resolve().parents[2]
    / "packages/usa-wa-adapter-legislature/src/usa_wa_adapter_legislature/data/initial_jurisdictions.json"
)

JURISDICTION_TYPES: tuple[tuple[str, str], ...] = (
    ("country", "Country"),
    ("state", "State"),
    ("county", "County"),
    ("city", "City"),
    ("legislative_district", "Legislative District"),
    ("legislative_district_upper", "Legislative District (Upper Chamber)"),
    ("legislative_district_lower", "Legislative District (Lower Chamber)"),
    ("congressional_district", "Congressional District"),
    ("judicial_district", "Judicial District"),
    ("school_district", "School District"),
    ("water_district", "Water District"),
    ("tribal_nation", "Tribal Nation"),
    ("federal_enclave", "Federal Enclave"),
    ("census_block", "Census Block"),
    ("census_tract", "Census Tract"),
    ("other", "Other"),
)

JURISDICTION_RELATIONSHIP_TYPES: tuple[tuple[str, str, str, bool], ...] = (
    ("is_fully_contained_by", "Is fully contained by", "spatial", False),
    ("partially_overlaps", "Partially overlaps", "spatial", True),
    ("is_coterminous_with", "Is coterminous with", "spatial", True),
    ("has_regulatory_authority_over", "Has regulatory authority over", "governance", False),
    (
        "has_extraterritorial_jurisdiction_over",
        "Has extraterritorial jurisdiction over",
        "governance",
        False,
    ),
    ("member_of", "Member of", "functional", False),
    ("reports_to", "Reports to", "functional", False),
    ("contracts_services_from", "Contracts services from", "functional", False),
    ("supersedes", "Supersedes", "temporal", False),
    ("succeeded_by", "Succeeded by", "temporal", False),
    ("evolved_from", "Evolved from", "temporal", False),
)

# (schema, table, unique-constraints-to-reseat). Each UQ entry is
# (constraint_name, columns_in_order). The migration drops each UQ before
# altering the column type and re-creates it after the FK is added so the
# new ULID column participates in the natural key.
JURISDICTION_FK_TABLES: tuple[tuple[str, str, tuple[tuple[str, tuple[str, ...]], ...]], ...] = (
    (CC, "document_identifiers", (
        ("uq_document_identifiers_natural_key",
         ("jurisdiction_id", "source", "source_id")),
        ("uq_document_identifiers_jurisdiction_entity_scheme_value",
         ("jurisdiction_id", "entity_type", "scheme", "value")),
    )),
    (CANONICAL, "amendments", (
        ("uq_amendments_natural_key", ("jurisdiction_id", "source", "source_id")),
    )),
    (CANONICAL, "assignments", (
        ("uq_assignments_natural_key", ("jurisdiction_id", "source", "source_id")),
    )),
    (CANONICAL, "bill_action_classifications", (
        ("uq_bill_action_classifications_natural_key",
         ("jurisdiction_id", "source", "source_id")),
    )),
    (CANONICAL, "bill_actions", (
        ("uq_bill_actions_natural_key", ("jurisdiction_id", "source", "source_id")),
    )),
    (CANONICAL, "bill_events", (
        ("uq_bill_events_natural_key", ("jurisdiction_id", "source", "source_id")),
    )),
    (CANONICAL, "bill_relationship_types", (
        ("uq_bill_relationship_types_jurisdiction_code", ("jurisdiction_id", "code")),
    )),
    (CANONICAL, "bill_relationships", (
        ("uq_bill_relationships_natural_key", ("jurisdiction_id", "source", "source_id")),
    )),
    (CANONICAL, "bill_sponsorships", (
        ("uq_bill_sponsorships_natural_key", ("jurisdiction_id", "source", "source_id")),
    )),
    (CANONICAL, "bill_statute_changes", ()),
    (CANONICAL, "bill_statutory_citations", (
        ("uq_bill_statutory_citations_natural_key",
         ("jurisdiction_id", "source", "source_id")),
    )),
    (CANONICAL, "bill_subjects", (
        ("uq_bill_subjects_natural_key", ("jurisdiction_id", "source", "source_id")),
    )),
    (CANONICAL, "bill_supplements", (
        ("uq_bill_supplements_natural_key", ("jurisdiction_id", "source", "source_id")),
    )),
    (CANONICAL, "bill_titles", (
        ("uq_bill_titles_natural_key", ("jurisdiction_id", "source", "source_id")),
    )),
    (CANONICAL, "bill_types", (
        ("uq_bill_types_jurisdiction_code", ("jurisdiction_id", "code")),
    )),
    (CANONICAL, "bill_version_links", (
        ("uq_bill_version_links_natural_key",
         ("jurisdiction_id", "source", "source_id")),
    )),
    (CANONICAL, "bill_versions", (
        ("uq_bill_versions_natural_key", ("jurisdiction_id", "source", "source_id")),
    )),
    (CANONICAL, "bills", (
        ("uq_bills_natural_key", ("jurisdiction_id", "source", "source_id")),
    )),
    (CANONICAL, "contributions", (
        ("uq_contributions_natural_key", ("jurisdiction_id", "source", "source_id")),
    )),
    (CANONICAL, "legislative_sessions", (
        ("uq_legislative_sessions_slug", ("jurisdiction_id", "slug")),
        ("uq_legislative_sessions_natural_key",
         ("jurisdiction_id", "source", "source_id")),
    )),
    (CANONICAL, "lobbying_activities", (
        ("uq_lobbying_activities_natural_key",
         ("jurisdiction_id", "source", "source_id")),
    )),
    (CANONICAL, "lobbying_positions", (
        ("uq_lobbying_positions_natural_key",
         ("jurisdiction_id", "source", "source_id")),
    )),
    (CANONICAL, "organization_identifiers", (
        ("uq_organization_identifiers_natural_key",
         ("jurisdiction_id", "source", "source_id")),
        ("uq_organization_identifiers_jurisdiction_scheme_value",
         ("jurisdiction_id", "scheme", "value")),
    )),
    (CANONICAL, "organizations", (
        ("uq_organizations_natural_key", ("jurisdiction_id", "source", "source_id")),
    )),
    (CANONICAL, "person_identifiers", (
        ("uq_person_identifiers_natural_key",
         ("jurisdiction_id", "source", "source_id")),
        ("uq_person_identifiers_jurisdiction_scheme_value",
         ("jurisdiction_id", "scheme", "value")),
    )),
    (CANONICAL, "person_votes", (
        ("uq_person_votes_natural_key", ("jurisdiction_id", "source", "source_id")),
    )),
    (CANONICAL, "persons", (
        ("uq_persons_natural_key", ("jurisdiction_id", "source", "source_id")),
    )),
    # roles handled separately below because it also drops `district`
    (CANONICAL, "statute_chapters", ()),
    (CANONICAL, "statute_codes", (
        ("uq_statute_codes_natural_key", ("jurisdiction_id", "code")),
    )),
    (CANONICAL, "statute_sections", ()),
    (CANONICAL, "statute_titles", ()),
    (CANONICAL, "vote_counts", (
        ("uq_vote_counts_natural_key", ("jurisdiction_id", "source", "source_id")),
    )),
    (CANONICAL, "vote_events", (
        ("uq_vote_events_natural_key", ("jurisdiction_id", "source", "source_id")),
    )),
)


def _refactor_jurisdiction_id_column(schema: str, table: str, uqs: Sequence) -> None:
    """Swap ``jurisdiction_id`` from VARCHAR(32) to ULID FK via temp column.

    PostgreSQL forbids subqueries inside ``ALTER COLUMN ... TYPE ... USING``, so
    we add a temporary nullable column, UPDATE-backfill via slug→ULID lookup
    (subqueries are allowed in UPDATE), drop the old column with its UQs and
    auto-created index, rename the temp column in, mark it NOT NULL, add the
    FK + the equivalent index, then re-create the UQs.
    """
    # Add temp column to hold backfilled ULID
    op.add_column(
        table,
        sa.Column("_jurisdiction_id_new", clearinghouse_core.db.ulid.ULID(), nullable=True),
        schema=schema,
    )
    # Backfill via slug → ULID lookup
    op.execute(
        f'UPDATE "{schema}"."{table}" '
        f'SET _jurisdiction_id_new = ('
        f"  SELECT id FROM clearinghouse_core.jurisdictions "
        f'  WHERE slug = "{schema}"."{table}".jurisdiction_id)'
    )
    # Guard: every row's jurisdiction_id slug must resolve to a cache row. A
    # row left with NULL _jurisdiction_id_new is an unmatched slug — likely a
    # gap in the bootstrap pre-seed. Fail loudly with the offending value
    # instead of letting the later NOT NULL ALTER fail opaquely.
    op.execute(
        sa.text(
            f"""
            DO $$
            DECLARE
                unmatched text;
            BEGIN
                -- Empty tables (the normal P0.5 state) produce unmatched=NULL,
                -- and the IF below is skipped — intentional no-op.
                SELECT string_agg(DISTINCT jurisdiction_id, ', ')
                  INTO unmatched
                  FROM "{schema}"."{table}"
                 WHERE _jurisdiction_id_new IS NULL;
                IF unmatched IS NOT NULL THEN
                    RAISE EXCEPTION
                      'jurisdictional IA refactor: unmatched jurisdiction_id slug(s) '
                      'in {schema}.{table}: %. Add the slug(s) to '
                      'initial_jurisdictions.json (or pre-seed them) before retrying.',
                      unmatched;
                END IF;
            END $$;
            """
        )
    )
    # Drop UQs that reference the old jurisdiction_id column
    for uq_name, _ in uqs:
        op.drop_constraint(uq_name, table, schema=schema, type_="unique")
    # Drop the old text column; the index ix_<schema>_<table>_jurisdiction_id
    # auto-generated from index=True goes with it.
    op.drop_column(table, "jurisdiction_id", schema=schema)
    # Rename temp → jurisdiction_id
    op.alter_column(
        table,
        "_jurisdiction_id_new",
        new_column_name="jurisdiction_id",
        schema=schema,
    )
    # Mark NOT NULL (now that backfill ran)
    op.alter_column(table, "jurisdiction_id", nullable=False, schema=schema)
    # Add FK
    op.create_foreign_key(
        f"fk_{table}_jurisdiction_id",
        table,
        "jurisdictions",
        ["jurisdiction_id"],
        ["id"],
        source_schema=schema,
        referent_schema=CC,
        ondelete="RESTRICT",
    )
    # Recreate the index that the column carried (index=True at model level)
    op.create_index(
        op.f(f"ix_{schema}_{table}_jurisdiction_id"),
        table,
        ["jurisdiction_id"],
        schema=schema,
    )
    # Recreate UQs (they now use the new ULID column transparently)
    for uq_name, uq_cols in uqs:
        op.create_unique_constraint(uq_name, table, list(uq_cols), schema=schema)


def _revert_jurisdiction_id_column(schema: str, table: str, uqs: Sequence) -> None:
    """Mirror of :func:`_refactor_jurisdiction_id_column` for downgrade."""
    for uq_name, _ in uqs:
        op.drop_constraint(uq_name, table, schema=schema, type_="unique")
    op.drop_index(
        op.f(f"ix_{schema}_{table}_jurisdiction_id"),
        table_name=table,
        schema=schema,
    )
    op.drop_constraint(
        f"fk_{table}_jurisdiction_id", table, schema=schema, type_="foreignkey"
    )
    op.add_column(
        table,
        sa.Column("_jurisdiction_id_old", sa.String(length=32), nullable=True),
        schema=schema,
    )
    op.execute(
        f'UPDATE "{schema}"."{table}" '
        f'SET _jurisdiction_id_old = ('
        f"  SELECT slug FROM clearinghouse_core.jurisdictions "
        f'  WHERE id = "{schema}"."{table}".jurisdiction_id)'
    )
    op.drop_column(table, "jurisdiction_id", schema=schema)
    op.alter_column(
        table,
        "_jurisdiction_id_old",
        new_column_name="jurisdiction_id",
        schema=schema,
    )
    op.alter_column(table, "jurisdiction_id", nullable=False, schema=schema)
    op.create_index(
        op.f(f"ix_{schema}_{table}_jurisdiction_id"),
        table,
        ["jurisdiction_id"],
        schema=schema,
    )
    for uq_name, uq_cols in uqs:
        op.create_unique_constraint(uq_name, table, list(uq_cols), schema=schema)


def upgrade() -> None:
    # ── Phase 1: create lookup tables (must exist before jurisdictions.type_id FK) ──
    op.create_table(
        "jurisdiction_types",
        sa.Column("id", clearinghouse_core.db.ulid.ULID(), nullable=False),
        sa.Column("slug", sa.String(length=64), nullable=False),
        sa.Column("display_name", sa.String(length=128), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("slug", name="uq_jurisdiction_types_slug"),
        schema=CC,
    )
    op.create_table(
        "jurisdiction_relationship_types",
        sa.Column("id", clearinghouse_core.db.ulid.ULID(), nullable=False),
        sa.Column("code", sa.String(length=64), nullable=False),
        sa.Column("display_name", sa.String(length=128), nullable=False),
        sa.Column("category", sa.String(length=16), nullable=False),
        sa.Column("is_symmetric", sa.Boolean(), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("code", name="uq_jurisdiction_relationship_types_code"),
        schema=CC,
    )

    # ── Phase 2: seed lookups (16 + 11 rows) with Python-generated ULIDs
    #            (matches the project convention from feedback_db_ulid + the
    #            clearinghouse_core_init seed at 2026_05_26_chcore_init.py:157;
    #            preserves ULID time-prefix ordering on the new B-tree indexes) ──
    for slug, display in JURISDICTION_TYPES:
        op.execute(
            sa.text(
                "INSERT INTO clearinghouse_core.jurisdiction_types (id, slug, display_name) "
                "VALUES (:id, :slug, :display) ON CONFLICT (slug) DO NOTHING"
            ).bindparams(id=_ULID().to_uuid(), slug=slug, display=display)
        )
    for code, display, category, is_symmetric in JURISDICTION_RELATIONSHIP_TYPES:
        op.execute(
            sa.text(
                "INSERT INTO clearinghouse_core.jurisdiction_relationship_types "
                "(id, code, display_name, category, is_symmetric) VALUES "
                "(:id, :code, :display, :category, :is_symmetric) "
                "ON CONFLICT (code) DO NOTHING"
            ).bindparams(
                id=_ULID().to_uuid(),
                code=code,
                display=display,
                category=category,
                is_symmetric=is_symmetric,
            )
        )

    # ── Phase 3: extend the existing clearinghouse_core.jurisdictions table ──
    op.add_column(
        "jurisdictions",
        sa.Column("pm_jurisdiction_id", clearinghouse_core.db.ulid.ULID(), nullable=True),
        schema=CC,
    )
    op.add_column(
        "jurisdictions",
        sa.Column("type_id", clearinghouse_core.db.ulid.ULID(), nullable=True),
        schema=CC,
    )
    op.add_column(
        "jurisdictions",
        sa.Column("valid_from", sa.DateTime(timezone=True), nullable=True),
        schema=CC,
    )
    op.add_column(
        "jurisdictions",
        sa.Column("valid_until", sa.DateTime(timezone=True), nullable=True),
        schema=CC,
    )
    op.add_column(
        "jurisdictions",
        sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=True),
        schema=CC,
    )
    op.add_column(
        "jurisdictions",
        sa.Column("superseded_at", sa.DateTime(timezone=True), nullable=True),
        schema=CC,
    )
    # Backfill any pre-existing rows: they're 'state'-level (only 'usa-wa' exists
    # in dev today). Set type_id from the lookup and stamp recorded_at = now().
    op.execute(
        "UPDATE clearinghouse_core.jurisdictions "
        "SET type_id = (SELECT id FROM clearinghouse_core.jurisdiction_types WHERE slug = 'state'), "
        "    recorded_at = now() "
        "WHERE type_id IS NULL"
    )
    op.drop_column("jurisdictions", "level", schema=CC)
    op.alter_column(
        "jurisdictions",
        "slug",
        type_=sa.String(length=128),
        existing_type=sa.String(length=64),
        existing_nullable=False,
        schema=CC,
    )
    op.alter_column(
        "jurisdictions",
        "name",
        type_=sa.Text(),
        existing_type=sa.String(length=128),
        existing_nullable=False,
        schema=CC,
    )
    op.alter_column("jurisdictions", "type_id", nullable=False, schema=CC)
    op.alter_column("jurisdictions", "recorded_at", nullable=False, schema=CC)
    op.create_foreign_key(
        "fk_jurisdictions_type_id",
        "jurisdictions",
        "jurisdiction_types",
        ["type_id"],
        ["id"],
        source_schema=CC,
        referent_schema=CC,
        ondelete="RESTRICT",
    )
    op.create_index(
        op.f("ix_clearinghouse_core_jurisdictions_pm_jurisdiction_id"),
        "jurisdictions",
        ["pm_jurisdiction_id"],
        schema=CC,
    )

    # ── Phase 4: create jurisdiction_relationships ──
    op.create_table(
        "jurisdiction_relationships",
        sa.Column("id", clearinghouse_core.db.ulid.ULID(), nullable=False),
        sa.Column("pm_relationship_id", clearinghouse_core.db.ulid.ULID(), nullable=True),
        sa.Column(
            "subject_jurisdiction_id",
            clearinghouse_core.db.ulid.ULID(),
            nullable=False,
        ),
        sa.Column(
            "object_jurisdiction_id",
            clearinghouse_core.db.ulid.ULID(),
            nullable=False,
        ),
        sa.Column(
            "relationship_type_id",
            clearinghouse_core.db.ulid.ULID(),
            nullable=False,
        ),
        sa.Column("metadata", sa.dialects.postgresql.JSONB(), nullable=True),
        sa.Column("valid_from", sa.DateTime(timezone=True), nullable=True),
        sa.Column("valid_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("superseded_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["subject_jurisdiction_id"],
            [f"{CC}.jurisdictions.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["object_jurisdiction_id"],
            [f"{CC}.jurisdictions.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["relationship_type_id"],
            [f"{CC}.jurisdiction_relationship_types.id"],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "subject_jurisdiction_id",
            "object_jurisdiction_id",
            "relationship_type_id",
            "valid_from",
            name="uq_jurisdiction_relationships_natural_key",
        ),
        schema=CC,
    )
    op.create_index(
        op.f("ix_clearinghouse_core_jurisdiction_relationships_subject_jurisdiction_id"),
        "jurisdiction_relationships",
        ["subject_jurisdiction_id"],
        schema=CC,
    )
    op.create_index(
        op.f("ix_clearinghouse_core_jurisdiction_relationships_object_jurisdiction_id"),
        "jurisdiction_relationships",
        ["object_jurisdiction_id"],
        schema=CC,
    )
    op.create_index(
        op.f("ix_clearinghouse_core_jurisdiction_relationships_relationship_type_id"),
        "jurisdiction_relationships",
        ["relationship_type_id"],
        schema=CC,
    )
    op.create_index(
        op.f("ix_clearinghouse_core_jurisdiction_relationships_pm_relationship_id"),
        "jurisdiction_relationships",
        ["pm_relationship_id"],
        schema=CC,
    )
    # Partial unique index closing PostgreSQL's NULL-distinct UNIQUE gap on the
    # natural key — without this, two rows with the same (s, o, rt) and NULL
    # valid_from could both insert.
    op.create_index(
        "uq_jurisdiction_relationships_natural_key_null_from",
        "jurisdiction_relationships",
        ["subject_jurisdiction_id", "object_jurisdiction_id", "relationship_type_id"],
        unique=True,
        postgresql_where=sa.text("valid_from IS NULL"),
        schema=CC,
    )

    # ── Phase 5: pre-seed 101 jurisdictions + 101 relationships from JSON ──
    with JSON_SEED_PATH.open() as f:
        seed = json.load(f)
    # Python-generated ULIDs (per feedback_db_ulid project convention). Re-runs
    # remain idempotent via the ON CONFLICT clauses below.
    for j in seed["jurisdictions"]:
        op.execute(
            sa.text(
                "INSERT INTO clearinghouse_core.jurisdictions "
                "(id, slug, name, type_id, recorded_at) "
                "VALUES (:id, :slug, :name, "
                "  (SELECT id FROM clearinghouse_core.jurisdiction_types WHERE slug = :type), "
                "  now()) "
                "ON CONFLICT (slug) DO NOTHING"
            ).bindparams(
                id=_ULID().to_uuid(), slug=j["slug"], name=j["name"], type=j["type"]
            )
        )
    for r in seed["relationships"]:
        op.execute(
            sa.text(
                "INSERT INTO clearinghouse_core.jurisdiction_relationships "
                "(id, subject_jurisdiction_id, object_jurisdiction_id, "
                "  relationship_type_id, recorded_at) "
                "VALUES (:id, "
                "  (SELECT id FROM clearinghouse_core.jurisdictions WHERE slug = :subj), "
                "  (SELECT id FROM clearinghouse_core.jurisdictions WHERE slug = :obj), "
                "  (SELECT id FROM clearinghouse_core.jurisdiction_relationship_types "
                "   WHERE code = :rel), "
                "  now()) "
                "ON CONFLICT DO NOTHING"
            ).bindparams(
                id=_ULID().to_uuid(),
                subj=r["subject_slug"],
                obj=r["object_slug"],
                rel=r["relationship_type"],
            )
        )

    # ── Phase 6: schema-wide jurisdiction_id text → ULID FK refactor ──
    # For every affected canonical table, drop its jurisdiction_id-bearing UQs,
    # ALTER COLUMN TYPE via slug→ULID lookup, add FK, recreate the UQs.
    for schema, table, uqs in JURISDICTION_FK_TABLES:
        _refactor_jurisdiction_id_column(schema, table, uqs)

    # ── Phase 7: roles special handling — refactor jurisdiction_id then drop district ──
    # The roles table also carries the `district` text(32) column which is
    # collapsing into Role.jurisdiction_id (an LD-21 cache row directly), so
    # the uq_roles_org_name_district UQ becomes uq_roles_org_name.
    #
    # CANNOT use _refactor_jurisdiction_id_column directly: PG auto-drops every
    # constraint that references a column when the column is dropped, so the
    # uq_roles_org_name_district UQ would vanish as a side effect of dropping
    # jurisdiction_id — leaving _refactor's recreate-UQs loop to fail (or this
    # block's later drop_constraint to fail on a missing constraint). Drop
    # uq_roles_org_name_district explicitly up front so it's not in PG's
    # auto-drop set, then run the refactor against only the natural_key UQ.
    op.drop_constraint(
        "uq_roles_org_name_district", "roles", schema=CANONICAL, type_="unique"
    )
    _refactor_jurisdiction_id_column(
        CANONICAL,
        "roles",
        (("uq_roles_natural_key", ("jurisdiction_id", "source", "source_id")),),
    )
    op.drop_column("roles", "district", schema=CANONICAL)
    op.create_unique_constraint(
        "uq_roles_org_name",
        "roles",
        ["jurisdiction_id", "organization_id", "name"],
        schema=CANONICAL,
    )


def downgrade() -> None:
    """Best-effort rollback for an immediately-applied migration.

    Drops FKs + recreates VARCHAR(32) columns; does NOT preserve relationships
    or restore the slug strings on canonical tables (the inline slug→ULID
    ALTER COLUMN is not generally reversible without a separate backfill).
    Use only for emergency rollback against freshly-bootstrap data.
    """
    # ── Roles reverse (re-add district, then reverse the jurisdiction_id refactor) ──
    op.add_column(
        "roles", sa.Column("district", sa.String(length=32), nullable=True), schema=CANONICAL
    )
    op.drop_constraint("uq_roles_org_name", "roles", schema=CANONICAL, type_="unique")
    _revert_jurisdiction_id_column(
        CANONICAL,
        "roles",
        (("uq_roles_natural_key", ("jurisdiction_id", "source", "source_id")),),
    )
    op.create_unique_constraint(
        "uq_roles_org_name_district",
        "roles",
        ["jurisdiction_id", "organization_id", "name", "district"],
        schema=CANONICAL,
    )

    # ── Reverse jurisdiction_id refactor on every other affected table ──
    for schema, table, uqs in reversed(JURISDICTION_FK_TABLES):
        _revert_jurisdiction_id_column(schema, table, uqs)

    # ── Reverse jurisdiction_relationships ──
    op.drop_index(
        "uq_jurisdiction_relationships_natural_key_null_from",
        table_name="jurisdiction_relationships",
        schema=CC,
    )
    op.drop_index(
        op.f("ix_clearinghouse_core_jurisdiction_relationships_pm_relationship_id"),
        table_name="jurisdiction_relationships",
        schema=CC,
    )
    op.drop_index(
        op.f("ix_clearinghouse_core_jurisdiction_relationships_relationship_type_id"),
        table_name="jurisdiction_relationships",
        schema=CC,
    )
    op.drop_index(
        op.f("ix_clearinghouse_core_jurisdiction_relationships_object_jurisdiction_id"),
        table_name="jurisdiction_relationships",
        schema=CC,
    )
    op.drop_index(
        op.f("ix_clearinghouse_core_jurisdiction_relationships_subject_jurisdiction_id"),
        table_name="jurisdiction_relationships",
        schema=CC,
    )
    op.drop_table("jurisdiction_relationships", schema=CC)

    # ── Reverse jurisdictions extension ──
    op.drop_index(
        op.f("ix_clearinghouse_core_jurisdictions_pm_jurisdiction_id"),
        table_name="jurisdictions",
        schema=CC,
    )
    op.drop_constraint(
        "fk_jurisdictions_type_id", "jurisdictions", schema=CC, type_="foreignkey"
    )
    op.add_column(
        "jurisdictions", sa.Column("level", sa.String(length=16), nullable=True), schema=CC
    )
    op.execute("UPDATE clearinghouse_core.jurisdictions SET level = 'state' WHERE level IS NULL")
    op.alter_column("jurisdictions", "level", nullable=False, schema=CC)
    op.alter_column(
        "jurisdictions",
        "name",
        type_=sa.String(length=128),
        existing_type=sa.Text(),
        existing_nullable=False,
        schema=CC,
    )
    op.alter_column(
        "jurisdictions",
        "slug",
        type_=sa.String(length=64),
        existing_type=sa.String(length=128),
        existing_nullable=False,
        schema=CC,
    )
    op.drop_column("jurisdictions", "superseded_at", schema=CC)
    op.drop_column("jurisdictions", "recorded_at", schema=CC)
    op.drop_column("jurisdictions", "valid_until", schema=CC)
    op.drop_column("jurisdictions", "valid_from", schema=CC)
    op.drop_column("jurisdictions", "type_id", schema=CC)
    op.drop_column("jurisdictions", "pm_jurisdiction_id", schema=CC)

    # ── Reverse lookup tables ──
    op.drop_table("jurisdiction_relationship_types", schema=CC)
    op.drop_table("jurisdiction_types", schema=CC)
