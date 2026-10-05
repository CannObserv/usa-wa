"""412 PR F: drop the canonical tier and the provenance spine

The destructive end of #412. Every writer of these tables stopped in PR E, the nightly
ran seven green nights without them (2026-09-29 to 10-05), and this PR deletes the code
that wrote and read them. A ``pg_dump`` of the whole database was taken first, after the
final ``raw_export`` carried the last payload into the raw store and the file sweep
verified it: ``/var/backups/usa_wa-412-predrop-20261005T172128Z.dump``.

**The canonical schema**, 33 tables: persons, organizations, roles, assignments and
their identifiers — 3,135 persons, 8,849 assignments, read by nothing since the parity
probes retired — and the declared-not-implemented bill, vote, statute, session and
lobbying clusters (#194), which never held a row. The four ``pm_*`` anchor columns
(#314) go with their tables. Dropped with ``DROP SCHEMA … CASCADE`` behind a run-time
check that nothing outside the schema depends on it.

**The provenance spine** in ``clearinghouse_core``: ``fetch_events`` (4,781),
``raw_payloads`` (1,435 bodies, every one verified present in the raw store by hash on
2026-10-05), ``citations`` (40,623), and the empty ``notes`` and
``document_identifiers``. Plus ``integrity_sweep_state`` (the Postgres sweep's cursor;
PR C moved the sweep to the raw store) and the PM jurisdiction mirror's half:
``jurisdiction_relationships`` (101 rows), its type lookup (11), and
``jurisdictions.pm_jurisdiction_id``.

**One column that FK'd the spine**: ``source_coverage.evidence_citation_id``, never set
(Q5), dropped first so its target can go.

What stays: ``sources`` + ``source_coverage`` (``/sources``), ``jurisdictions`` +
``jurisdiction_types`` (Q4, the FK target ``sources`` names), ``job_runs``, the
``registry`` schema and ``serving``.

The downgrade recreates every structure, empty — the chain stays reversible either side
of this revision, as #314 step C's does. The rows come back only from the dump.

Revision ID: f9efb48842d6
Revises: 746b53a33587
Create Date: 2026-10-05 17:59:40.176077

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = 'f9efb48842d6'
down_revision: Union[str, Sequence[str], None] = '746b53a33587'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Drop the canonical schema, then the provenance spine and the PM jurisdiction mirror."""
    # Q5: the one surviving column that FKs into the spine, before its target goes. It
    # never carried a value (0 of 7 rows, 2026-10-05).
    op.drop_constraint(
        "source_coverage_evidence_citation_id_fkey",
        "source_coverage",
        schema="clearinghouse_core",
        type_="foreignkey",
    )
    op.drop_column("source_coverage", "evidence_citation_id", schema="clearinghouse_core")

    # The canonical schema whole, CASCADE: 33 tables whose foreign keys point at one
    # another and out at clearinghouse_core.jurisdictions, never in from outside. That was
    # verified before this was written, and it is asserted here rather than assumed: a
    # CASCADE that found a dependant outside the schema would take it too, silently.
    outside = op.get_bind().execute(sa.text(_OUTSIDE_DEPENDANTS)).scalars().all()
    if outside:
        raise RuntimeError(f"objects outside canonical depend on it: {outside}")
    op.execute("DROP SCHEMA IF EXISTS canonical CASCADE")

    # The provenance spine and its satellites, children first. drop_table takes each
    # table's indexes with it.
    for table in (
        "citations",
        "raw_payloads",
        "fetch_events",
        "notes",
        "document_identifiers",
        "integrity_sweep_state",
        "jurisdiction_relationships",
        "jurisdiction_relationship_types",
    ):
        op.drop_table(table, schema="clearinghouse_core")

    # The PM mirror's anchor on the surviving jurisdictions table (its index goes with it).
    op.drop_column("jurisdictions", "pm_jurisdiction_id", schema="clearinghouse_core")


#: Anything outside ``canonical`` that a CASCADE on the schema would also drop: a foreign
#: key from another schema's table, or a view over one of its tables.
_OUTSIDE_DEPENDANTS = """
SELECT conrelid::regclass::text FROM pg_constraint
 WHERE contype = 'f'
   AND confrelid IN (SELECT oid FROM pg_class WHERE relnamespace = 'canonical'::regnamespace)
   AND conrelid NOT IN (SELECT oid FROM pg_class WHERE relnamespace = 'canonical'::regnamespace)
UNION
SELECT DISTINCT view_schema || '.' || view_name FROM information_schema.view_table_usage
 WHERE table_schema = 'canonical' AND view_schema <> 'canonical'
"""


def downgrade() -> None:
    """Recreate every structure the upgrade dropped, empty. The rows do not come back.

    Ordered by foreign key, with the one cycle (``bills.current_version_id`` ->
    ``bill_versions``) closed by a foreign key added after both tables exist.
    """
    op.execute("CREATE SCHEMA IF NOT EXISTS canonical")
    op.create_table('bill_types',
    sa.Column('id', sa.UUID(), autoincrement=False, nullable=False),
    sa.Column('code', sa.VARCHAR(length=32), autoincrement=False, nullable=False),
    sa.Column('display_name', sa.VARCHAR(length=128), autoincrement=False, nullable=False),
    sa.Column('classification', sa.VARCHAR(length=32), autoincrement=False, nullable=True),
    sa.Column('created_at', postgresql.TIMESTAMP(timezone=True), server_default=sa.text('now()'), autoincrement=False, nullable=False),
    sa.Column('updated_at', postgresql.TIMESTAMP(timezone=True), server_default=sa.text('now()'), autoincrement=False, nullable=False),
    sa.Column('jurisdiction_id', sa.UUID(), autoincrement=False, nullable=False),
    sa.ForeignKeyConstraint(['jurisdiction_id'], ['clearinghouse_core.jurisdictions.id'], name=op.f('fk_bill_types_jurisdiction_id'), ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id', name=op.f('bill_types_pkey')),
    sa.UniqueConstraint('jurisdiction_id', 'code', name=op.f('uq_bill_types_jurisdiction_code'), postgresql_include=[], postgresql_nulls_not_distinct=False),
    schema='canonical'
    )
    op.create_index(op.f('ix_canonical_bill_types_jurisdiction_id'), 'bill_types', ['jurisdiction_id'], unique=False, schema='canonical')
    op.create_table('organizations',
    sa.Column('id', sa.UUID(), autoincrement=False, nullable=False),
    sa.Column('source', sa.VARCHAR(length=64), autoincrement=False, nullable=False),
    sa.Column('source_id', sa.VARCHAR(length=128), autoincrement=False, nullable=False),
    sa.Column('name', sa.VARCHAR(length=512), autoincrement=False, nullable=False),
    sa.Column('short_name', sa.VARCHAR(length=128), autoincrement=False, nullable=True),
    sa.Column('org_type', sa.VARCHAR(length=32), autoincrement=False, nullable=False),
    sa.Column('parent_organization_id', sa.UUID(), autoincrement=False, nullable=True),
    sa.Column('pm_organization_id', sa.UUID(), autoincrement=False, nullable=True),
    sa.Column('updated_at', postgresql.TIMESTAMP(timezone=True), server_default=sa.text('now()'), autoincrement=False, nullable=False),
    sa.Column('created_at', postgresql.TIMESTAMP(timezone=True), server_default=sa.text('now()'), autoincrement=False, nullable=False),
    sa.Column('jurisdiction_id', sa.UUID(), autoincrement=False, nullable=True),
    sa.Column('acronym', sa.VARCHAR(length=64), autoincrement=False, nullable=True),
    sa.Column('phone', sa.VARCHAR(length=64), autoincrement=False, nullable=True),
    sa.Column('archived_at', postgresql.TIMESTAMP(timezone=True), autoincrement=False, nullable=True),
    sa.Column('deleted_at', postgresql.TIMESTAMP(timezone=True), autoincrement=False, nullable=True),
    sa.Column('active', sa.BOOLEAN(), server_default=sa.text('true'), autoincrement=False, nullable=False),
    sa.ForeignKeyConstraint(['jurisdiction_id'], ['clearinghouse_core.jurisdictions.id'], name=op.f('fk_organizations_jurisdiction_id'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['parent_organization_id'], ['canonical.organizations.id'], name=op.f('organizations_parent_organization_id_fkey'), ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id', name=op.f('organizations_pkey')),
    sa.UniqueConstraint('source', 'source_id', name=op.f('uq_organizations_natural_key'), postgresql_include=[], postgresql_nulls_not_distinct=False),
    schema='canonical'
    )
    op.create_index(op.f('uq_organizations_pm_organization_id'), 'organizations', ['pm_organization_id'], unique=True, schema='canonical', postgresql_where='(pm_organization_id IS NOT NULL)')
    op.create_index(op.f('ix_canonical_organizations_parent_organization_id'), 'organizations', ['parent_organization_id'], unique=False, schema='canonical')
    op.create_index(op.f('ix_canonical_organizations_jurisdiction_id'), 'organizations', ['jurisdiction_id'], unique=False, schema='canonical')
    op.create_table('legislative_sessions',
    sa.Column('id', sa.UUID(), autoincrement=False, nullable=False),
    sa.Column('source', sa.VARCHAR(length=64), autoincrement=False, nullable=False),
    sa.Column('source_id', sa.VARCHAR(length=128), autoincrement=False, nullable=False),
    sa.Column('slug', sa.VARCHAR(length=64), autoincrement=False, nullable=False),
    sa.Column('name', sa.TEXT(), autoincrement=False, nullable=False),
    sa.Column('classification', sa.VARCHAR(length=32), autoincrement=False, nullable=False),
    sa.Column('start_date', sa.DATE(), autoincrement=False, nullable=True),
    sa.Column('end_date', sa.DATE(), autoincrement=False, nullable=True),
    sa.Column('is_active', sa.BOOLEAN(), autoincrement=False, nullable=False),
    sa.Column('biennium_label', sa.VARCHAR(length=16), autoincrement=False, nullable=True),
    sa.Column('updated_at', postgresql.TIMESTAMP(timezone=True), server_default=sa.text('now()'), autoincrement=False, nullable=False),
    sa.Column('created_at', postgresql.TIMESTAMP(timezone=True), server_default=sa.text('now()'), autoincrement=False, nullable=False),
    sa.Column('organization_id', sa.UUID(), autoincrement=False, nullable=False),
    sa.Column('parent_legislative_session_id', sa.UUID(), autoincrement=False, nullable=True),
    sa.ForeignKeyConstraint(['organization_id'], ['canonical.organizations.id'], name=op.f('fk_legislative_sessions_organization_id'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['parent_legislative_session_id'], ['canonical.legislative_sessions.id'], name=op.f('fk_legislative_sessions_parent_legislative_session_id'), ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id', name=op.f('legislative_sessions_pkey')),
    sa.UniqueConstraint('organization_id', 'slug', name=op.f('uq_legislative_sessions_slug'), postgresql_include=[], postgresql_nulls_not_distinct=False),
    sa.UniqueConstraint('source', 'source_id', name=op.f('uq_legislative_sessions_natural_key'), postgresql_include=[], postgresql_nulls_not_distinct=False),
    schema='canonical'
    )
    op.create_index(op.f('ix_canonical_legislative_sessions_parent_legislative_session_id'), 'legislative_sessions', ['parent_legislative_session_id'], unique=False, schema='canonical')
    op.create_index(op.f('ix_canonical_legislative_sessions_organization_id'), 'legislative_sessions', ['organization_id'], unique=False, schema='canonical')
    op.create_table('bills',
    sa.Column('id', sa.UUID(), autoincrement=False, nullable=False),
    sa.Column('source', sa.VARCHAR(length=64), autoincrement=False, nullable=False),
    sa.Column('source_id', sa.VARCHAR(length=128), autoincrement=False, nullable=False),
    sa.Column('legislative_session_id', sa.UUID(), autoincrement=False, nullable=False),
    sa.Column('number', sa.INTEGER(), autoincrement=False, nullable=False),
    sa.Column('title', sa.TEXT(), autoincrement=False, nullable=False),
    sa.Column('current_status', sa.VARCHAR(length=128), autoincrement=False, nullable=True),
    sa.Column('current_status_class', sa.VARCHAR(length=32), autoincrement=False, nullable=True),
    sa.Column('current_status_at', postgresql.TIMESTAMP(timezone=True), autoincrement=False, nullable=True),
    sa.Column('introduced_at', postgresql.TIMESTAMP(timezone=True), autoincrement=False, nullable=True),
    sa.Column('enacted_as', sa.VARCHAR(length=64), autoincrement=False, nullable=True),
    sa.Column('updated_at', postgresql.TIMESTAMP(timezone=True), server_default=sa.text('now()'), autoincrement=False, nullable=False),
    sa.Column('created_at', postgresql.TIMESTAMP(timezone=True), server_default=sa.text('now()'), autoincrement=False, nullable=False),
    sa.Column('current_version_id', sa.UUID(), autoincrement=False, nullable=True),
    sa.Column('originating_chamber_id', sa.UUID(), autoincrement=False, nullable=False),
    sa.Column('current_chamber_id', sa.UUID(), autoincrement=False, nullable=True),
    sa.Column('bill_type_id', sa.UUID(), autoincrement=False, nullable=True),
    sa.ForeignKeyConstraint(['bill_type_id'], ['canonical.bill_types.id'], name=op.f('fk_bills_bill_type_id'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['current_chamber_id'], ['canonical.organizations.id'], name=op.f('fk_bills_current_chamber_id'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['legislative_session_id'], ['canonical.legislative_sessions.id'], name=op.f('bills_legislative_session_id_fkey'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['originating_chamber_id'], ['canonical.organizations.id'], name=op.f('fk_bills_originating_chamber_id'), ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id', name=op.f('bills_pkey')),
    sa.UniqueConstraint('source', 'source_id', name=op.f('uq_bills_natural_key'), postgresql_include=[], postgresql_nulls_not_distinct=False),
    schema='canonical'
    )
    op.create_index(op.f('ix_canonical_bills_originating_chamber_id'), 'bills', ['originating_chamber_id'], unique=False, schema='canonical')
    op.create_index(op.f('ix_canonical_bills_legislative_session_id'), 'bills', ['legislative_session_id'], unique=False, schema='canonical')
    op.create_index(op.f('ix_canonical_bills_current_chamber_id'), 'bills', ['current_chamber_id'], unique=False, schema='canonical')
    op.create_index(op.f('ix_canonical_bills_bill_type_id'), 'bills', ['bill_type_id'], unique=False, schema='canonical')
    op.create_table('bill_versions',
    sa.Column('id', sa.UUID(), autoincrement=False, nullable=False),
    sa.Column('source', sa.VARCHAR(length=64), autoincrement=False, nullable=False),
    sa.Column('source_id', sa.VARCHAR(length=128), autoincrement=False, nullable=False),
    sa.Column('bill_id', sa.UUID(), autoincrement=False, nullable=False),
    sa.Column('version_type', sa.VARCHAR(length=64), autoincrement=False, nullable=False),
    sa.Column('version_at', postgresql.TIMESTAMP(timezone=True), autoincrement=False, nullable=True),
    sa.Column('is_current', sa.BOOLEAN(), autoincrement=False, nullable=False),
    sa.Column('updated_at', postgresql.TIMESTAMP(timezone=True), server_default=sa.text('now()'), autoincrement=False, nullable=False),
    sa.Column('created_at', postgresql.TIMESTAMP(timezone=True), server_default=sa.text('now()'), autoincrement=False, nullable=False),
    sa.Column('short_description', sa.TEXT(), autoincrement=False, nullable=True),
    sa.Column('text', sa.TEXT(), autoincrement=False, nullable=True),
    sa.ForeignKeyConstraint(['bill_id'], ['canonical.bills.id'], name=op.f('bill_versions_bill_id_fkey'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('bill_versions_pkey')),
    sa.UniqueConstraint('source', 'source_id', name=op.f('uq_bill_versions_natural_key'), postgresql_include=[], postgresql_nulls_not_distinct=False),
    schema='canonical'
    )
    op.create_index(op.f('ix_canonical_bill_versions_bill_id'), 'bill_versions', ['bill_id'], unique=False, schema='canonical')
    op.create_table('persons',
    sa.Column('id', sa.UUID(), autoincrement=False, nullable=False),
    sa.Column('source', sa.VARCHAR(length=64), autoincrement=False, nullable=False),
    sa.Column('source_id', sa.VARCHAR(length=128), autoincrement=False, nullable=False),
    sa.Column('name_full', sa.VARCHAR(length=256), autoincrement=False, nullable=False),
    sa.Column('name_first', sa.VARCHAR(length=128), autoincrement=False, nullable=True),
    sa.Column('name_last', sa.VARCHAR(length=128), autoincrement=False, nullable=True),
    sa.Column('name_middle', sa.VARCHAR(length=128), autoincrement=False, nullable=True),
    sa.Column('name_suffix', sa.VARCHAR(length=32), autoincrement=False, nullable=True),
    sa.Column('name_used', sa.VARCHAR(length=256), autoincrement=False, nullable=True),
    sa.Column('gender', sa.VARCHAR(length=32), autoincrement=False, nullable=True),
    sa.Column('pm_person_id', sa.UUID(), autoincrement=False, nullable=True),
    sa.Column('updated_at', postgresql.TIMESTAMP(timezone=True), server_default=sa.text('now()'), autoincrement=False, nullable=False),
    sa.Column('created_at', postgresql.TIMESTAMP(timezone=True), server_default=sa.text('now()'), autoincrement=False, nullable=False),
    sa.Column('archived_at', postgresql.TIMESTAMP(timezone=True), autoincrement=False, nullable=True),
    sa.Column('deleted_at', postgresql.TIMESTAMP(timezone=True), autoincrement=False, nullable=True),
    sa.PrimaryKeyConstraint('id', name=op.f('persons_pkey')),
    sa.UniqueConstraint('source', 'source_id', name=op.f('uq_persons_natural_key'), postgresql_include=[], postgresql_nulls_not_distinct=False),
    schema='canonical'
    )
    op.create_index(op.f('uq_persons_pm_person_id'), 'persons', ['pm_person_id'], unique=True, schema='canonical', postgresql_where='(pm_person_id IS NOT NULL)')
    op.create_table('amendments',
    sa.Column('id', sa.UUID(), autoincrement=False, nullable=False),
    sa.Column('source', sa.VARCHAR(length=64), autoincrement=False, nullable=False),
    sa.Column('source_id', sa.VARCHAR(length=128), autoincrement=False, nullable=False),
    sa.Column('label', sa.VARCHAR(length=64), autoincrement=False, nullable=False),
    sa.Column('sponsor_person_id', sa.UUID(), autoincrement=False, nullable=True),
    sa.Column('sponsor_organization_id', sa.UUID(), autoincrement=False, nullable=True),
    sa.Column('status', sa.VARCHAR(length=32), autoincrement=False, nullable=False),
    sa.Column('offered_at', postgresql.TIMESTAMP(timezone=True), autoincrement=False, nullable=True),
    sa.Column('adopted_at', postgresql.TIMESTAMP(timezone=True), autoincrement=False, nullable=True),
    sa.Column('rejected_at', postgresql.TIMESTAMP(timezone=True), autoincrement=False, nullable=True),
    sa.Column('withdrawn_at', postgresql.TIMESTAMP(timezone=True), autoincrement=False, nullable=True),
    sa.Column('updated_at', postgresql.TIMESTAMP(timezone=True), server_default=sa.text('now()'), autoincrement=False, nullable=False),
    sa.Column('created_at', postgresql.TIMESTAMP(timezone=True), server_default=sa.text('now()'), autoincrement=False, nullable=False),
    sa.Column('amendment_kind', sa.VARCHAR(length=16), autoincrement=False, nullable=False),
    sa.Column('bill_version_id', sa.UUID(), autoincrement=False, nullable=False),
    sa.ForeignKeyConstraint(['bill_version_id'], ['canonical.bill_versions.id'], name=op.f('fk_amendments_bill_version_id'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['sponsor_organization_id'], ['canonical.organizations.id'], name=op.f('amendments_sponsor_organization_id_fkey'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['sponsor_person_id'], ['canonical.persons.id'], name=op.f('amendments_sponsor_person_id_fkey'), ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id', name=op.f('amendments_pkey')),
    sa.UniqueConstraint('source', 'source_id', name=op.f('uq_amendments_natural_key'), postgresql_include=[], postgresql_nulls_not_distinct=False),
    schema='canonical'
    )
    op.create_index(op.f('ix_canonical_amendments_bill_version_id'), 'amendments', ['bill_version_id'], unique=False, schema='canonical')
    op.create_table('bill_supplements',
    sa.Column('id', sa.UUID(), autoincrement=False, nullable=False),
    sa.Column('source', sa.VARCHAR(length=64), autoincrement=False, nullable=False),
    sa.Column('source_id', sa.VARCHAR(length=128), autoincrement=False, nullable=False),
    sa.Column('bill_version_id', sa.UUID(), autoincrement=False, nullable=False),
    sa.Column('bill_id', sa.UUID(), autoincrement=False, nullable=False),
    sa.Column('supplement_kind', sa.VARCHAR(length=32), autoincrement=False, nullable=False),
    sa.Column('status', sa.VARCHAR(length=16), autoincrement=False, nullable=True),
    sa.Column('revision_sequence', sa.INTEGER(), server_default=sa.text('1'), autoincrement=False, nullable=False),
    sa.Column('title', sa.VARCHAR(length=512), autoincrement=False, nullable=True),
    sa.Column('author_organization_id', sa.UUID(), autoincrement=False, nullable=True),
    sa.Column('published_at', postgresql.TIMESTAMP(timezone=True), autoincrement=False, nullable=False),
    sa.Column('url', sa.VARCHAR(length=2048), autoincrement=False, nullable=True),
    sa.Column('mime_type', sa.VARCHAR(length=128), autoincrement=False, nullable=True),
    sa.Column('text', sa.TEXT(), autoincrement=False, nullable=True),
    sa.Column('structured_data', postgresql.JSONB(astext_type=sa.Text()), autoincrement=False, nullable=True),
    sa.Column('archival_url', sa.VARCHAR(length=2048), autoincrement=False, nullable=True),
    sa.Column('archived_at', postgresql.TIMESTAMP(timezone=True), autoincrement=False, nullable=True),
    sa.Column('created_at', postgresql.TIMESTAMP(timezone=True), server_default=sa.text('now()'), autoincrement=False, nullable=False),
    sa.Column('updated_at', postgresql.TIMESTAMP(timezone=True), server_default=sa.text('now()'), autoincrement=False, nullable=False),
    sa.ForeignKeyConstraint(['author_organization_id'], ['canonical.organizations.id'], name=op.f('bill_supplements_author_organization_id_fkey'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['bill_id'], ['canonical.bills.id'], name=op.f('bill_supplements_bill_id_fkey'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['bill_version_id'], ['canonical.bill_versions.id'], name=op.f('bill_supplements_bill_version_id_fkey'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('bill_supplements_pkey')),
    sa.UniqueConstraint('bill_version_id', 'supplement_kind', 'status', 'revision_sequence', name=op.f('uq_bill_supplements_content_key'), postgresql_include=[], postgresql_nulls_not_distinct=False),
    sa.UniqueConstraint('source', 'source_id', name=op.f('uq_bill_supplements_natural_key'), postgresql_include=[], postgresql_nulls_not_distinct=False),
    schema='canonical'
    )
    op.create_index(op.f('ix_canonical_bill_supplements_bill_version_id'), 'bill_supplements', ['bill_version_id'], unique=False, schema='canonical')
    op.create_index(op.f('ix_canonical_bill_supplements_bill_id'), 'bill_supplements', ['bill_id'], unique=False, schema='canonical')
    op.create_index(op.f('ix_canonical_bill_supplements_author_organization_id'), 'bill_supplements', ['author_organization_id'], unique=False, schema='canonical')
    op.create_table('bill_actions',
    sa.Column('id', sa.UUID(), autoincrement=False, nullable=False),
    sa.Column('source', sa.VARCHAR(length=64), autoincrement=False, nullable=False),
    sa.Column('source_id', sa.VARCHAR(length=128), autoincrement=False, nullable=False),
    sa.Column('bill_id', sa.UUID(), autoincrement=False, nullable=False),
    sa.Column('action_at', postgresql.TIMESTAMP(timezone=True), autoincrement=False, nullable=False),
    sa.Column('acting_organization_id', sa.UUID(), autoincrement=False, nullable=True),
    sa.Column('action_type', sa.VARCHAR(length=64), autoincrement=False, nullable=False),
    sa.Column('primary_classification', sa.VARCHAR(length=64), autoincrement=False, nullable=True),
    sa.Column('description', sa.TEXT(), autoincrement=False, nullable=False),
    sa.Column('display_order', sa.INTEGER(), autoincrement=False, nullable=True),
    sa.Column('is_major', sa.BOOLEAN(), autoincrement=False, nullable=False),
    sa.Column('updated_at', postgresql.TIMESTAMP(timezone=True), server_default=sa.text('now()'), autoincrement=False, nullable=False),
    sa.Column('created_at', postgresql.TIMESTAMP(timezone=True), server_default=sa.text('now()'), autoincrement=False, nullable=False),
    sa.Column('supplement_id', sa.UUID(), autoincrement=False, nullable=True),
    sa.ForeignKeyConstraint(['acting_organization_id'], ['canonical.organizations.id'], name=op.f('bill_actions_acting_organization_id_fkey'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['bill_id'], ['canonical.bills.id'], name=op.f('bill_actions_bill_id_fkey'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['supplement_id'], ['canonical.bill_supplements.id'], name=op.f('fk_bill_actions_supplement_id'), ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id', name=op.f('bill_actions_pkey')),
    sa.UniqueConstraint('source', 'source_id', name=op.f('uq_bill_actions_natural_key'), postgresql_include=[], postgresql_nulls_not_distinct=False),
    schema='canonical'
    )
    op.create_index(op.f('ix_canonical_bill_actions_supplement_id'), 'bill_actions', ['supplement_id'], unique=False, schema='canonical')
    op.create_index(op.f('ix_canonical_bill_actions_bill_id'), 'bill_actions', ['bill_id'], unique=False, schema='canonical')
    op.create_index(op.f('ix_canonical_bill_actions_acting_organization_id'), 'bill_actions', ['acting_organization_id'], unique=False, schema='canonical')
    op.create_table('vote_events',
    sa.Column('id', sa.UUID(), autoincrement=False, nullable=False),
    sa.Column('source', sa.VARCHAR(length=64), autoincrement=False, nullable=False),
    sa.Column('source_id', sa.VARCHAR(length=128), autoincrement=False, nullable=False),
    sa.Column('subject_type', sa.VARCHAR(length=16), autoincrement=False, nullable=False),
    sa.Column('subject_id', sa.UUID(), autoincrement=False, nullable=False),
    sa.Column('bill_id', sa.UUID(), autoincrement=False, nullable=True),
    sa.Column('amendment_id', sa.UUID(), autoincrement=False, nullable=True),
    sa.Column('motion_description', sa.TEXT(), autoincrement=False, nullable=True),
    sa.Column('context_type', sa.VARCHAR(length=16), autoincrement=False, nullable=False),
    sa.Column('context_organization_id', sa.UUID(), autoincrement=False, nullable=False),
    sa.Column('chamber', sa.VARCHAR(length=16), autoincrement=False, nullable=True),
    sa.Column('category', sa.VARCHAR(length=32), autoincrement=False, nullable=True),
    sa.Column('event_at', postgresql.TIMESTAMP(timezone=True), autoincrement=False, nullable=False),
    sa.Column('outcome', sa.VARCHAR(length=32), autoincrement=False, nullable=False),
    sa.Column('updated_at', postgresql.TIMESTAMP(timezone=True), server_default=sa.text('now()'), autoincrement=False, nullable=False),
    sa.Column('created_at', postgresql.TIMESTAMP(timezone=True), server_default=sa.text('now()'), autoincrement=False, nullable=False),
    sa.Column('originating_bill_action_id', sa.UUID(), autoincrement=False, nullable=True),
    sa.ForeignKeyConstraint(['amendment_id'], ['canonical.amendments.id'], name=op.f('vote_events_amendment_id_fkey'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['bill_id'], ['canonical.bills.id'], name=op.f('vote_events_bill_id_fkey'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['context_organization_id'], ['canonical.organizations.id'], name=op.f('vote_events_context_organization_id_fkey'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['originating_bill_action_id'], ['canonical.bill_actions.id'], name=op.f('fk_vote_events_originating_bill_action_id'), ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id', name=op.f('vote_events_pkey')),
    sa.UniqueConstraint('source', 'source_id', name=op.f('uq_vote_events_natural_key'), postgresql_include=[], postgresql_nulls_not_distinct=False),
    schema='canonical'
    )
    op.create_index(op.f('ix_canonical_vote_events_originating_bill_action_id'), 'vote_events', ['originating_bill_action_id'], unique=False, schema='canonical')
    op.create_index(op.f('ix_canonical_vote_events_context_organization_id'), 'vote_events', ['context_organization_id'], unique=False, schema='canonical')
    op.create_index(op.f('ix_canonical_vote_events_bill_id'), 'vote_events', ['bill_id'], unique=False, schema='canonical')
    op.create_index(op.f('ix_canonical_vote_events_amendment_id'), 'vote_events', ['amendment_id'], unique=False, schema='canonical')
    op.create_table('fetch_events',
    sa.Column('id', sa.UUID(), autoincrement=False, nullable=False),
    sa.Column('source_id', sa.UUID(), autoincrement=False, nullable=False),
    sa.Column('resource_id', sa.VARCHAR(length=256), autoincrement=False, nullable=False),
    sa.Column('resource_version_key', sa.VARCHAR(length=256), autoincrement=False, nullable=True),
    sa.Column('url', sa.VARCHAR(length=2048), autoincrement=False, nullable=False),
    sa.Column('fetched_at', postgresql.TIMESTAMP(timezone=True), autoincrement=False, nullable=False),
    sa.Column('http_status', sa.INTEGER(), autoincrement=False, nullable=True),
    sa.Column('content_hash', postgresql.BYTEA(), autoincrement=False, nullable=True),
    sa.Column('etag', sa.VARCHAR(length=256), autoincrement=False, nullable=True),
    sa.Column('last_modified', sa.VARCHAR(length=64), autoincrement=False, nullable=True),
    sa.Column('status', sa.VARCHAR(length=16), autoincrement=False, nullable=False),
    sa.Column('created_at', postgresql.TIMESTAMP(timezone=True), server_default=sa.text('now()'), autoincrement=False, nullable=False),
    sa.ForeignKeyConstraint(['source_id'], ['clearinghouse_core.sources.id'], name=op.f('fetch_events_source_id_fkey'), ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id', name=op.f('fetch_events_pkey')),
    schema='clearinghouse_core'
    )
    op.create_index(op.f('ix_clearinghouse_core_fetch_events_resource_id'), 'fetch_events', ['resource_id'], unique=False, schema='clearinghouse_core')
    op.create_index(op.f('ix_clearinghouse_core_fetch_events_dedup'), 'fetch_events', ['source_id', 'resource_id', 'content_hash'], unique=False, schema='clearinghouse_core')
    op.create_table('raw_payloads',
    sa.Column('id', sa.UUID(), autoincrement=False, nullable=False),
    sa.Column('fetch_event_id', sa.UUID(), autoincrement=False, nullable=False),
    sa.Column('content_type', sa.VARCHAR(length=128), autoincrement=False, nullable=False),
    sa.Column('body', postgresql.BYTEA(), autoincrement=False, nullable=False),
    sa.Column('size_bytes', sa.BIGINT(), autoincrement=False, nullable=False),
    sa.Column('created_at', postgresql.TIMESTAMP(timezone=True), server_default=sa.text('now()'), autoincrement=False, nullable=False),
    sa.ForeignKeyConstraint(['fetch_event_id'], ['clearinghouse_core.fetch_events.id'], name=op.f('raw_payloads_fetch_event_id_fkey'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('raw_payloads_pkey')),
    sa.UniqueConstraint('fetch_event_id', name=op.f('uq_raw_payloads_fetch_event'), postgresql_include=[], postgresql_nulls_not_distinct=False),
    schema='clearinghouse_core'
    )
    op.create_table('statute_codes',
    sa.Column('id', sa.UUID(), autoincrement=False, nullable=False),
    sa.Column('code', sa.VARCHAR(length=32), autoincrement=False, nullable=False),
    sa.Column('name', sa.VARCHAR(length=256), autoincrement=False, nullable=False),
    sa.Column('description', sa.TEXT(), autoincrement=False, nullable=True),
    sa.Column('updated_at', postgresql.TIMESTAMP(timezone=True), server_default=sa.text('now()'), autoincrement=False, nullable=False),
    sa.Column('created_at', postgresql.TIMESTAMP(timezone=True), server_default=sa.text('now()'), autoincrement=False, nullable=False),
    sa.Column('jurisdiction_id', sa.UUID(), autoincrement=False, nullable=False),
    sa.ForeignKeyConstraint(['jurisdiction_id'], ['clearinghouse_core.jurisdictions.id'], name=op.f('fk_statute_codes_jurisdiction_id'), ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id', name=op.f('statute_codes_pkey')),
    sa.UniqueConstraint('jurisdiction_id', 'code', name=op.f('uq_statute_codes_natural_key'), postgresql_include=[], postgresql_nulls_not_distinct=False),
    schema='canonical'
    )
    op.create_index(op.f('ix_canonical_statute_codes_jurisdiction_id'), 'statute_codes', ['jurisdiction_id'], unique=False, schema='canonical')
    op.create_table('statute_titles',
    sa.Column('id', sa.UUID(), autoincrement=False, nullable=False),
    sa.Column('statute_code_id', sa.UUID(), autoincrement=False, nullable=False),
    sa.Column('number', sa.VARCHAR(length=32), autoincrement=False, nullable=False),
    sa.Column('heading', sa.TEXT(), autoincrement=False, nullable=False),
    sa.Column('updated_at', postgresql.TIMESTAMP(timezone=True), server_default=sa.text('now()'), autoincrement=False, nullable=False),
    sa.Column('created_at', postgresql.TIMESTAMP(timezone=True), server_default=sa.text('now()'), autoincrement=False, nullable=False),
    sa.Column('jurisdiction_id', sa.UUID(), autoincrement=False, nullable=False),
    sa.ForeignKeyConstraint(['jurisdiction_id'], ['clearinghouse_core.jurisdictions.id'], name=op.f('fk_statute_titles_jurisdiction_id'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['statute_code_id'], ['canonical.statute_codes.id'], name=op.f('statute_titles_statute_code_id_fkey'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('statute_titles_pkey')),
    sa.UniqueConstraint('statute_code_id', 'number', name=op.f('uq_statute_titles_natural_key'), postgresql_include=[], postgresql_nulls_not_distinct=False),
    schema='canonical'
    )
    op.create_index(op.f('ix_canonical_statute_titles_statute_code_id'), 'statute_titles', ['statute_code_id'], unique=False, schema='canonical')
    op.create_index(op.f('ix_canonical_statute_titles_jurisdiction_id'), 'statute_titles', ['jurisdiction_id'], unique=False, schema='canonical')
    op.create_table('statute_chapters',
    sa.Column('id', sa.UUID(), autoincrement=False, nullable=False),
    sa.Column('statute_title_id', sa.UUID(), autoincrement=False, nullable=False),
    sa.Column('number', sa.VARCHAR(length=32), autoincrement=False, nullable=False),
    sa.Column('heading', sa.TEXT(), autoincrement=False, nullable=False),
    sa.Column('updated_at', postgresql.TIMESTAMP(timezone=True), server_default=sa.text('now()'), autoincrement=False, nullable=False),
    sa.Column('created_at', postgresql.TIMESTAMP(timezone=True), server_default=sa.text('now()'), autoincrement=False, nullable=False),
    sa.Column('jurisdiction_id', sa.UUID(), autoincrement=False, nullable=False),
    sa.ForeignKeyConstraint(['jurisdiction_id'], ['clearinghouse_core.jurisdictions.id'], name=op.f('fk_statute_chapters_jurisdiction_id'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['statute_title_id'], ['canonical.statute_titles.id'], name=op.f('statute_chapters_statute_title_id_fkey'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('statute_chapters_pkey')),
    sa.UniqueConstraint('statute_title_id', 'number', name=op.f('uq_statute_chapters_natural_key'), postgresql_include=[], postgresql_nulls_not_distinct=False),
    schema='canonical'
    )
    op.create_index(op.f('ix_canonical_statute_chapters_statute_title_id'), 'statute_chapters', ['statute_title_id'], unique=False, schema='canonical')
    op.create_index(op.f('ix_canonical_statute_chapters_jurisdiction_id'), 'statute_chapters', ['jurisdiction_id'], unique=False, schema='canonical')
    op.create_table('statute_sections',
    sa.Column('id', sa.UUID(), autoincrement=False, nullable=False),
    sa.Column('statute_chapter_id', sa.UUID(), autoincrement=False, nullable=False),
    sa.Column('number', sa.VARCHAR(length=64), autoincrement=False, nullable=False),
    sa.Column('heading', sa.TEXT(), autoincrement=False, nullable=False),
    sa.Column('text', sa.TEXT(), autoincrement=False, nullable=True),
    sa.Column('updated_at', postgresql.TIMESTAMP(timezone=True), server_default=sa.text('now()'), autoincrement=False, nullable=False),
    sa.Column('created_at', postgresql.TIMESTAMP(timezone=True), server_default=sa.text('now()'), autoincrement=False, nullable=False),
    sa.Column('jurisdiction_id', sa.UUID(), autoincrement=False, nullable=False),
    sa.ForeignKeyConstraint(['jurisdiction_id'], ['clearinghouse_core.jurisdictions.id'], name=op.f('fk_statute_sections_jurisdiction_id'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['statute_chapter_id'], ['canonical.statute_chapters.id'], name=op.f('statute_sections_statute_chapter_id_fkey'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('statute_sections_pkey')),
    sa.UniqueConstraint('statute_chapter_id', 'number', name=op.f('uq_statute_sections_natural_key'), postgresql_include=[], postgresql_nulls_not_distinct=False),
    schema='canonical'
    )
    op.create_index(op.f('ix_canonical_statute_sections_statute_chapter_id'), 'statute_sections', ['statute_chapter_id'], unique=False, schema='canonical')
    op.create_index(op.f('ix_canonical_statute_sections_jurisdiction_id'), 'statute_sections', ['jurisdiction_id'], unique=False, schema='canonical')
    op.create_table('contributions',
    sa.Column('id', sa.UUID(), autoincrement=False, nullable=False),
    sa.Column('source', sa.VARCHAR(length=64), autoincrement=False, nullable=False),
    sa.Column('source_id', sa.VARCHAR(length=128), autoincrement=False, nullable=False),
    sa.Column('recipient_organization_id', sa.UUID(), autoincrement=False, nullable=False),
    sa.Column('contributor_person_id', sa.UUID(), autoincrement=False, nullable=True),
    sa.Column('contributor_organization_id', sa.UUID(), autoincrement=False, nullable=True),
    sa.Column('contributor_name_raw', sa.VARCHAR(length=512), autoincrement=False, nullable=True),
    sa.Column('amount', sa.NUMERIC(precision=14, scale=2), autoincrement=False, nullable=False),
    sa.Column('contributed_at', postgresql.TIMESTAMP(timezone=True), autoincrement=False, nullable=False),
    sa.Column('updated_at', postgresql.TIMESTAMP(timezone=True), server_default=sa.text('now()'), autoincrement=False, nullable=False),
    sa.Column('created_at', postgresql.TIMESTAMP(timezone=True), server_default=sa.text('now()'), autoincrement=False, nullable=False),
    sa.Column('jurisdiction_id', sa.UUID(), autoincrement=False, nullable=False),
    sa.CheckConstraint('NOT (contributor_person_id IS NOT NULL AND contributor_organization_id IS NOT NULL)', name=op.f('ck_contributions_at_most_one_contributor')),
    sa.ForeignKeyConstraint(['contributor_organization_id'], ['canonical.organizations.id'], name=op.f('contributions_contributor_organization_id_fkey'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['contributor_person_id'], ['canonical.persons.id'], name=op.f('contributions_contributor_person_id_fkey'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['jurisdiction_id'], ['clearinghouse_core.jurisdictions.id'], name=op.f('fk_contributions_jurisdiction_id'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['recipient_organization_id'], ['canonical.organizations.id'], name=op.f('contributions_recipient_organization_id_fkey'), ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id', name=op.f('contributions_pkey')),
    sa.UniqueConstraint('jurisdiction_id', 'source', 'source_id', name=op.f('uq_contributions_natural_key'), postgresql_include=[], postgresql_nulls_not_distinct=False),
    schema='canonical'
    )
    op.create_index(op.f('ix_canonical_contributions_recipient_organization_id'), 'contributions', ['recipient_organization_id'], unique=False, schema='canonical')
    op.create_index(op.f('ix_canonical_contributions_jurisdiction_id'), 'contributions', ['jurisdiction_id'], unique=False, schema='canonical')
    op.create_index(op.f('ix_canonical_contributions_contributor_person_id'), 'contributions', ['contributor_person_id'], unique=False, schema='canonical')
    op.create_index(op.f('ix_canonical_contributions_contributor_organization_id'), 'contributions', ['contributor_organization_id'], unique=False, schema='canonical')
    op.create_table('person_identifiers',
    sa.Column('id', sa.UUID(), autoincrement=False, nullable=False),
    sa.Column('source', sa.VARCHAR(length=64), autoincrement=False, nullable=False),
    sa.Column('source_id', sa.VARCHAR(length=128), autoincrement=False, nullable=False),
    sa.Column('person_id', sa.UUID(), autoincrement=False, nullable=False),
    sa.Column('scheme', sa.VARCHAR(length=64), autoincrement=False, nullable=False),
    sa.Column('value', sa.VARCHAR(length=128), autoincrement=False, nullable=False),
    sa.Column('verified_at', sa.TEXT(), autoincrement=False, nullable=True),
    sa.Column('updated_at', postgresql.TIMESTAMP(timezone=True), server_default=sa.text('now()'), autoincrement=False, nullable=False),
    sa.Column('created_at', postgresql.TIMESTAMP(timezone=True), server_default=sa.text('now()'), autoincrement=False, nullable=False),
    sa.ForeignKeyConstraint(['person_id'], ['canonical.persons.id'], name=op.f('person_identifiers_person_id_fkey'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('person_identifiers_pkey')),
    sa.UniqueConstraint('person_id', 'scheme', name=op.f('uq_person_identifiers_person_scheme'), postgresql_include=[], postgresql_nulls_not_distinct=False),
    sa.UniqueConstraint('scheme', 'value', name=op.f('uq_person_identifiers_scheme_value'), postgresql_include=[], postgresql_nulls_not_distinct=False),
    sa.UniqueConstraint('source', 'source_id', name=op.f('uq_person_identifiers_natural_key'), postgresql_include=[], postgresql_nulls_not_distinct=False),
    schema='canonical'
    )
    op.create_index(op.f('ix_canonical_person_identifiers_person_id'), 'person_identifiers', ['person_id'], unique=False, schema='canonical')
    op.create_table('jurisdiction_relationship_types',
    sa.Column('id', sa.UUID(), autoincrement=False, nullable=False),
    sa.Column('code', sa.VARCHAR(length=64), autoincrement=False, nullable=False),
    sa.Column('display_name', sa.VARCHAR(length=128), autoincrement=False, nullable=False),
    sa.Column('category', sa.VARCHAR(length=16), autoincrement=False, nullable=False),
    sa.Column('is_symmetric', sa.BOOLEAN(), autoincrement=False, nullable=False),
    sa.Column('description', sa.TEXT(), autoincrement=False, nullable=True),
    sa.Column('created_at', postgresql.TIMESTAMP(timezone=True), server_default=sa.text('now()'), autoincrement=False, nullable=False),
    sa.PrimaryKeyConstraint('id', name=op.f('jurisdiction_relationship_types_pkey')),
    sa.UniqueConstraint('code', name=op.f('uq_jurisdiction_relationship_types_code'), postgresql_include=[], postgresql_nulls_not_distinct=False),
    schema='clearinghouse_core'
    )
    op.create_table('vote_counts',
    sa.Column('id', sa.UUID(), autoincrement=False, nullable=False),
    sa.Column('source', sa.VARCHAR(length=64), autoincrement=False, nullable=False),
    sa.Column('source_id', sa.VARCHAR(length=128), autoincrement=False, nullable=False),
    sa.Column('vote_event_id', sa.UUID(), autoincrement=False, nullable=False),
    sa.Column('count_type', sa.VARCHAR(length=16), autoincrement=False, nullable=False),
    sa.Column('value', sa.INTEGER(), autoincrement=False, nullable=False),
    sa.Column('updated_at', postgresql.TIMESTAMP(timezone=True), server_default=sa.text('now()'), autoincrement=False, nullable=False),
    sa.Column('created_at', postgresql.TIMESTAMP(timezone=True), server_default=sa.text('now()'), autoincrement=False, nullable=False),
    sa.ForeignKeyConstraint(['vote_event_id'], ['canonical.vote_events.id'], name=op.f('vote_counts_vote_event_id_fkey'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('vote_counts_pkey')),
    sa.UniqueConstraint('source', 'source_id', name=op.f('uq_vote_counts_natural_key'), postgresql_include=[], postgresql_nulls_not_distinct=False),
    sa.UniqueConstraint('vote_event_id', 'count_type', name=op.f('uq_vote_counts_event_type'), postgresql_include=[], postgresql_nulls_not_distinct=False),
    schema='canonical'
    )
    op.create_index(op.f('ix_canonical_vote_counts_vote_event_id'), 'vote_counts', ['vote_event_id'], unique=False, schema='canonical')
    op.create_table('roles',
    sa.Column('id', sa.UUID(), autoincrement=False, nullable=False),
    sa.Column('source', sa.VARCHAR(length=64), autoincrement=False, nullable=False),
    sa.Column('source_id', sa.VARCHAR(length=128), autoincrement=False, nullable=False),
    sa.Column('organization_id', sa.UUID(), autoincrement=False, nullable=False),
    sa.Column('name', sa.VARCHAR(length=64), autoincrement=False, nullable=False),
    sa.Column('role_type', sa.VARCHAR(length=32), autoincrement=False, nullable=False),
    sa.Column('updated_at', postgresql.TIMESTAMP(timezone=True), server_default=sa.text('now()'), autoincrement=False, nullable=False),
    sa.Column('created_at', postgresql.TIMESTAMP(timezone=True), server_default=sa.text('now()'), autoincrement=False, nullable=False),
    sa.Column('pm_role_id', sa.UUID(), autoincrement=False, nullable=True),
    sa.Column('archived_at', postgresql.TIMESTAMP(timezone=True), autoincrement=False, nullable=True),
    sa.Column('deleted_at', postgresql.TIMESTAMP(timezone=True), autoincrement=False, nullable=True),
    sa.Column('jurisdiction_id', sa.UUID(), autoincrement=False, nullable=True),
    sa.Column('qualifier', sa.VARCHAR(length=64), autoincrement=False, nullable=True),
    sa.ForeignKeyConstraint(['jurisdiction_id'], ['clearinghouse_core.jurisdictions.id'], name=op.f('fk_roles_jurisdiction_id'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['organization_id'], ['canonical.organizations.id'], name=op.f('roles_organization_id_fkey'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('roles_pkey')),
    sa.UniqueConstraint('source', 'source_id', name=op.f('uq_roles_natural_key'), postgresql_include=[], postgresql_nulls_not_distinct=False),
    schema='canonical'
    )
    op.create_index(op.f('uq_roles_seat'), 'roles', ['organization_id', 'role_type', 'jurisdiction_id', 'qualifier'], unique=True, schema='canonical', postgresql_where='(jurisdiction_id IS NOT NULL)', postgresql_nulls_not_distinct=True)
    op.create_index(op.f('uq_roles_pm_role_id'), 'roles', ['pm_role_id'], unique=True, schema='canonical', postgresql_where='(pm_role_id IS NOT NULL)')
    op.create_index(op.f('uq_roles_org_name'), 'roles', ['organization_id', 'name'], unique=True, schema='canonical', postgresql_where='(jurisdiction_id IS NULL)')
    op.create_index(op.f('ix_canonical_roles_organization_id'), 'roles', ['organization_id'], unique=False, schema='canonical')
    op.create_index(op.f('ix_canonical_roles_jurisdiction_id'), 'roles', ['jurisdiction_id'], unique=False, schema='canonical')
    op.create_table('assignments',
    sa.Column('id', sa.UUID(), autoincrement=False, nullable=False),
    sa.Column('source', sa.VARCHAR(length=64), autoincrement=False, nullable=False),
    sa.Column('source_id', sa.VARCHAR(length=128), autoincrement=False, nullable=False),
    sa.Column('person_id', sa.UUID(), autoincrement=False, nullable=True),
    sa.Column('holder_name_raw', sa.VARCHAR(length=256), autoincrement=False, nullable=True),
    sa.Column('role_id', sa.UUID(), autoincrement=False, nullable=False),
    sa.Column('valid_from', sa.DATE(), autoincrement=False, nullable=False),
    sa.Column('valid_to', sa.DATE(), autoincrement=False, nullable=True),
    sa.Column('is_active', sa.BOOLEAN(), autoincrement=False, nullable=False),
    sa.Column('updated_at', postgresql.TIMESTAMP(timezone=True), server_default=sa.text('now()'), autoincrement=False, nullable=False),
    sa.Column('created_at', postgresql.TIMESTAMP(timezone=True), server_default=sa.text('now()'), autoincrement=False, nullable=False),
    sa.Column('pm_assignment_id', sa.UUID(), autoincrement=False, nullable=True),
    sa.Column('archived_at', postgresql.TIMESTAMP(timezone=True), autoincrement=False, nullable=True),
    sa.Column('deleted_at', postgresql.TIMESTAMP(timezone=True), autoincrement=False, nullable=True),
    sa.CheckConstraint('person_id IS NOT NULL OR holder_name_raw IS NOT NULL', name=op.f('ck_assignments_person_or_name')),
    sa.ForeignKeyConstraint(['person_id'], ['canonical.persons.id'], name=op.f('assignments_person_id_fkey'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['role_id'], ['canonical.roles.id'], name=op.f('assignments_role_id_fkey'), ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id', name=op.f('assignments_pkey')),
    sa.UniqueConstraint('source', 'source_id', name=op.f('uq_assignments_natural_key'), postgresql_include=[], postgresql_nulls_not_distinct=False),
    schema='canonical'
    )
    op.create_index(op.f('uq_assignments_pm_assignment_id'), 'assignments', ['pm_assignment_id'], unique=True, schema='canonical', postgresql_where='(pm_assignment_id IS NOT NULL)')
    op.create_index(op.f('ix_canonical_assignments_role_id'), 'assignments', ['role_id'], unique=False, schema='canonical')
    op.create_index(op.f('ix_canonical_assignments_person_id'), 'assignments', ['person_id'], unique=False, schema='canonical')
    op.create_index(op.f('ix_assignments_span_kind'), 'assignments', [sa.literal_column("split_part(source_id::text, ':'::text, 2)"), 'id'], unique=False, schema='canonical')
    op.create_table('person_votes',
    sa.Column('id', sa.UUID(), autoincrement=False, nullable=False),
    sa.Column('source', sa.VARCHAR(length=64), autoincrement=False, nullable=False),
    sa.Column('source_id', sa.VARCHAR(length=128), autoincrement=False, nullable=False),
    sa.Column('vote_event_id', sa.UUID(), autoincrement=False, nullable=False),
    sa.Column('person_id', sa.UUID(), autoincrement=False, nullable=True),
    sa.Column('voter_name_raw', sa.VARCHAR(length=256), autoincrement=False, nullable=True),
    sa.Column('vote', sa.VARCHAR(length=16), autoincrement=False, nullable=False),
    sa.Column('updated_at', postgresql.TIMESTAMP(timezone=True), server_default=sa.text('now()'), autoincrement=False, nullable=False),
    sa.Column('created_at', postgresql.TIMESTAMP(timezone=True), server_default=sa.text('now()'), autoincrement=False, nullable=False),
    sa.CheckConstraint('person_id IS NOT NULL OR voter_name_raw IS NOT NULL', name=op.f('ck_person_votes_person_or_name')),
    sa.ForeignKeyConstraint(['person_id'], ['canonical.persons.id'], name=op.f('person_votes_person_id_fkey'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['vote_event_id'], ['canonical.vote_events.id'], name=op.f('person_votes_vote_event_id_fkey'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('person_votes_pkey')),
    sa.UniqueConstraint('source', 'source_id', name=op.f('uq_person_votes_natural_key'), postgresql_include=[], postgresql_nulls_not_distinct=False),
    schema='canonical'
    )
    op.create_index(op.f('ix_canonical_person_votes_vote_event_id'), 'person_votes', ['vote_event_id'], unique=False, schema='canonical')
    op.create_index(op.f('ix_canonical_person_votes_person_id'), 'person_votes', ['person_id'], unique=False, schema='canonical')
    op.create_table('organization_identifiers',
    sa.Column('id', sa.UUID(), autoincrement=False, nullable=False),
    sa.Column('source', sa.VARCHAR(length=64), autoincrement=False, nullable=False),
    sa.Column('source_id', sa.VARCHAR(length=128), autoincrement=False, nullable=False),
    sa.Column('organization_id', sa.UUID(), autoincrement=False, nullable=False),
    sa.Column('scheme', sa.VARCHAR(length=64), autoincrement=False, nullable=False),
    sa.Column('value', sa.VARCHAR(length=128), autoincrement=False, nullable=False),
    sa.Column('verified_at', sa.TEXT(), autoincrement=False, nullable=True),
    sa.Column('updated_at', postgresql.TIMESTAMP(timezone=True), server_default=sa.text('now()'), autoincrement=False, nullable=False),
    sa.Column('created_at', postgresql.TIMESTAMP(timezone=True), server_default=sa.text('now()'), autoincrement=False, nullable=False),
    sa.ForeignKeyConstraint(['organization_id'], ['canonical.organizations.id'], name=op.f('organization_identifiers_organization_id_fkey'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('organization_identifiers_pkey')),
    sa.UniqueConstraint('organization_id', 'scheme', name=op.f('uq_organization_identifiers_org_scheme'), postgresql_include=[], postgresql_nulls_not_distinct=False),
    sa.UniqueConstraint('scheme', 'value', name=op.f('uq_organization_identifiers_scheme_value'), postgresql_include=[], postgresql_nulls_not_distinct=False),
    sa.UniqueConstraint('source', 'source_id', name=op.f('uq_organization_identifiers_natural_key'), postgresql_include=[], postgresql_nulls_not_distinct=False),
    schema='canonical'
    )
    op.create_index(op.f('ix_canonical_organization_identifiers_organization_id'), 'organization_identifiers', ['organization_id'], unique=False, schema='canonical')
    op.create_table('jurisdiction_relationships',
    sa.Column('id', sa.UUID(), autoincrement=False, nullable=False),
    sa.Column('pm_relationship_id', sa.UUID(), autoincrement=False, nullable=True),
    sa.Column('subject_jurisdiction_id', sa.UUID(), autoincrement=False, nullable=False),
    sa.Column('object_jurisdiction_id', sa.UUID(), autoincrement=False, nullable=False),
    sa.Column('relationship_type_id', sa.UUID(), autoincrement=False, nullable=False),
    sa.Column('metadata', postgresql.JSONB(astext_type=sa.Text()), autoincrement=False, nullable=True),
    sa.Column('valid_from', postgresql.TIMESTAMP(timezone=True), autoincrement=False, nullable=True),
    sa.Column('valid_until', postgresql.TIMESTAMP(timezone=True), autoincrement=False, nullable=True),
    sa.Column('recorded_at', postgresql.TIMESTAMP(timezone=True), autoincrement=False, nullable=False),
    sa.Column('superseded_at', postgresql.TIMESTAMP(timezone=True), autoincrement=False, nullable=True),
    sa.Column('created_at', postgresql.TIMESTAMP(timezone=True), server_default=sa.text('now()'), autoincrement=False, nullable=False),
    sa.Column('updated_at', postgresql.TIMESTAMP(timezone=True), server_default=sa.text('now()'), autoincrement=False, nullable=False),
    sa.ForeignKeyConstraint(['object_jurisdiction_id'], ['clearinghouse_core.jurisdictions.id'], name=op.f('jurisdiction_relationships_object_jurisdiction_id_fkey'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['relationship_type_id'], ['clearinghouse_core.jurisdiction_relationship_types.id'], name=op.f('jurisdiction_relationships_relationship_type_id_fkey'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['subject_jurisdiction_id'], ['clearinghouse_core.jurisdictions.id'], name=op.f('jurisdiction_relationships_subject_jurisdiction_id_fkey'), ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id', name=op.f('jurisdiction_relationships_pkey')),
    sa.UniqueConstraint('subject_jurisdiction_id', 'object_jurisdiction_id', 'relationship_type_id', 'valid_from', name=op.f('uq_jurisdiction_relationships_natural_key'), postgresql_include=[], postgresql_nulls_not_distinct=False),
    schema='clearinghouse_core'
    )
    op.create_index(op.f('uq_jurisdiction_relationships_natural_key_null_from'), 'jurisdiction_relationships', ['subject_jurisdiction_id', 'object_jurisdiction_id', 'relationship_type_id'], unique=True, schema='clearinghouse_core', postgresql_where='(valid_from IS NULL)')
    op.create_index(op.f('ix_clearinghouse_core_jurisdiction_relationships_subjec_5319'), 'jurisdiction_relationships', ['subject_jurisdiction_id'], unique=False, schema='clearinghouse_core')
    op.create_index(op.f('ix_clearinghouse_core_jurisdiction_relationships_relati_ba90'), 'jurisdiction_relationships', ['relationship_type_id'], unique=False, schema='clearinghouse_core')
    op.create_index(op.f('ix_clearinghouse_core_jurisdiction_relationships_pm_rel_1dc3'), 'jurisdiction_relationships', ['pm_relationship_id'], unique=False, schema='clearinghouse_core')
    op.create_index(op.f('ix_clearinghouse_core_jurisdiction_relationships_object_85b9'), 'jurisdiction_relationships', ['object_jurisdiction_id'], unique=False, schema='clearinghouse_core')
    op.create_table('notes',
    sa.Column('id', sa.UUID(), autoincrement=False, nullable=False),
    sa.Column('entity_type', sa.VARCHAR(length=64), autoincrement=False, nullable=False),
    sa.Column('entity_id', sa.UUID(), autoincrement=False, nullable=False),
    sa.Column('note_kind', sa.VARCHAR(length=32), autoincrement=False, nullable=False),
    sa.Column('text', sa.TEXT(), autoincrement=False, nullable=False),
    sa.Column('author_person_id', sa.UUID(), autoincrement=False, nullable=True),
    sa.Column('author_organization_id', sa.UUID(), autoincrement=False, nullable=True),
    sa.Column('effective_at', postgresql.TIMESTAMP(timezone=True), autoincrement=False, nullable=True),
    sa.Column('created_at', postgresql.TIMESTAMP(timezone=True), server_default=sa.text('now()'), autoincrement=False, nullable=False),
    sa.Column('updated_at', postgresql.TIMESTAMP(timezone=True), server_default=sa.text('now()'), autoincrement=False, nullable=False),
    sa.PrimaryKeyConstraint('id', name=op.f('notes_pkey')),
    schema='clearinghouse_core'
    )
    op.create_index(op.f('ix_clearinghouse_core_notes_entity_type'), 'notes', ['entity_type'], unique=False, schema='clearinghouse_core')
    op.create_index(op.f('ix_clearinghouse_core_notes_entity_id'), 'notes', ['entity_id'], unique=False, schema='clearinghouse_core')
    op.create_table('lobbying_activities',
    sa.Column('id', sa.UUID(), autoincrement=False, nullable=False),
    sa.Column('source', sa.VARCHAR(length=64), autoincrement=False, nullable=False),
    sa.Column('source_id', sa.VARCHAR(length=128), autoincrement=False, nullable=False),
    sa.Column('person_id', sa.UUID(), autoincrement=False, nullable=True),
    sa.Column('organization_id', sa.UUID(), autoincrement=False, nullable=True),
    sa.Column('employer_organization_id', sa.UUID(), autoincrement=False, nullable=True),
    sa.Column('period_start', sa.DATE(), autoincrement=False, nullable=False),
    sa.Column('period_end', sa.DATE(), autoincrement=False, nullable=False),
    sa.Column('compensation', sa.NUMERIC(precision=14, scale=2), autoincrement=False, nullable=True),
    sa.Column('expenses', sa.NUMERIC(precision=14, scale=2), autoincrement=False, nullable=True),
    sa.Column('updated_at', postgresql.TIMESTAMP(timezone=True), server_default=sa.text('now()'), autoincrement=False, nullable=False),
    sa.Column('created_at', postgresql.TIMESTAMP(timezone=True), server_default=sa.text('now()'), autoincrement=False, nullable=False),
    sa.Column('jurisdiction_id', sa.UUID(), autoincrement=False, nullable=False),
    sa.CheckConstraint('person_id IS NOT NULL OR organization_id IS NOT NULL', name=op.f('ck_lobbying_activities_person_or_org')),
    sa.ForeignKeyConstraint(['employer_organization_id'], ['canonical.organizations.id'], name=op.f('lobbying_activities_employer_organization_id_fkey'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['jurisdiction_id'], ['clearinghouse_core.jurisdictions.id'], name=op.f('fk_lobbying_activities_jurisdiction_id'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['organization_id'], ['canonical.organizations.id'], name=op.f('lobbying_activities_organization_id_fkey'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['person_id'], ['canonical.persons.id'], name=op.f('lobbying_activities_person_id_fkey'), ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id', name=op.f('lobbying_activities_pkey')),
    sa.UniqueConstraint('jurisdiction_id', 'source', 'source_id', name=op.f('uq_lobbying_activities_natural_key'), postgresql_include=[], postgresql_nulls_not_distinct=False),
    schema='canonical'
    )
    op.create_index(op.f('ix_canonical_lobbying_activities_person_id'), 'lobbying_activities', ['person_id'], unique=False, schema='canonical')
    op.create_index(op.f('ix_canonical_lobbying_activities_organization_id'), 'lobbying_activities', ['organization_id'], unique=False, schema='canonical')
    op.create_index(op.f('ix_canonical_lobbying_activities_jurisdiction_id'), 'lobbying_activities', ['jurisdiction_id'], unique=False, schema='canonical')
    op.create_table('bill_statute_changes',
    sa.Column('id', sa.UUID(), autoincrement=False, nullable=False),
    sa.Column('bill_id', sa.UUID(), autoincrement=False, nullable=False),
    sa.Column('statute_section_id', sa.UUID(), autoincrement=False, nullable=False),
    sa.Column('change_type', sa.VARCHAR(length=32), autoincrement=False, nullable=False),
    sa.Column('updated_at', postgresql.TIMESTAMP(timezone=True), server_default=sa.text('now()'), autoincrement=False, nullable=False),
    sa.Column('created_at', postgresql.TIMESTAMP(timezone=True), server_default=sa.text('now()'), autoincrement=False, nullable=False),
    sa.Column('jurisdiction_id', sa.UUID(), autoincrement=False, nullable=False),
    sa.ForeignKeyConstraint(['bill_id'], ['canonical.bills.id'], name=op.f('bill_statute_changes_bill_id_fkey'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['jurisdiction_id'], ['clearinghouse_core.jurisdictions.id'], name=op.f('fk_bill_statute_changes_jurisdiction_id'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['statute_section_id'], ['canonical.statute_sections.id'], name=op.f('bill_statute_changes_statute_section_id_fkey'), ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id', name=op.f('bill_statute_changes_pkey')),
    sa.UniqueConstraint('bill_id', 'statute_section_id', 'change_type', name=op.f('uq_bill_statute_changes_natural_key'), postgresql_include=[], postgresql_nulls_not_distinct=False),
    schema='canonical'
    )
    op.create_index(op.f('ix_canonical_bill_statute_changes_statute_section_id'), 'bill_statute_changes', ['statute_section_id'], unique=False, schema='canonical')
    op.create_index(op.f('ix_canonical_bill_statute_changes_jurisdiction_id'), 'bill_statute_changes', ['jurisdiction_id'], unique=False, schema='canonical')
    op.create_index(op.f('ix_canonical_bill_statute_changes_bill_id'), 'bill_statute_changes', ['bill_id'], unique=False, schema='canonical')
    op.create_table('bill_sponsorships',
    sa.Column('id', sa.UUID(), autoincrement=False, nullable=False),
    sa.Column('source', sa.VARCHAR(length=64), autoincrement=False, nullable=False),
    sa.Column('source_id', sa.VARCHAR(length=128), autoincrement=False, nullable=False),
    sa.Column('bill_id', sa.UUID(), autoincrement=False, nullable=False),
    sa.Column('person_id', sa.UUID(), autoincrement=False, nullable=True),
    sa.Column('organization_id', sa.UUID(), autoincrement=False, nullable=True),
    sa.Column('sponsor_name_raw', sa.VARCHAR(length=256), autoincrement=False, nullable=True),
    sa.Column('role', sa.VARCHAR(length=32), autoincrement=False, nullable=False),
    sa.Column('sponsor_order', sa.INTEGER(), autoincrement=False, nullable=True),
    sa.Column('withdrawn_at', postgresql.TIMESTAMP(timezone=True), autoincrement=False, nullable=True),
    sa.Column('updated_at', postgresql.TIMESTAMP(timezone=True), server_default=sa.text('now()'), autoincrement=False, nullable=False),
    sa.Column('created_at', postgresql.TIMESTAMP(timezone=True), server_default=sa.text('now()'), autoincrement=False, nullable=False),
    sa.Column('sponsored_at', postgresql.TIMESTAMP(timezone=True), autoincrement=False, nullable=True),
    sa.CheckConstraint('person_id IS NOT NULL AND organization_id IS NULL OR person_id IS NULL AND organization_id IS NOT NULL OR person_id IS NULL AND organization_id IS NULL AND sponsor_name_raw IS NOT NULL', name=op.f('ck_bill_sponsorships_polymorphic')),
    sa.ForeignKeyConstraint(['bill_id'], ['canonical.bills.id'], name=op.f('bill_sponsorships_bill_id_fkey'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['organization_id'], ['canonical.organizations.id'], name=op.f('bill_sponsorships_organization_id_fkey'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['person_id'], ['canonical.persons.id'], name=op.f('bill_sponsorships_person_id_fkey'), ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id', name=op.f('bill_sponsorships_pkey')),
    sa.UniqueConstraint('source', 'source_id', name=op.f('uq_bill_sponsorships_natural_key'), postgresql_include=[], postgresql_nulls_not_distinct=False),
    schema='canonical'
    )
    op.create_index(op.f('ix_canonical_bill_sponsorships_bill_id'), 'bill_sponsorships', ['bill_id'], unique=False, schema='canonical')
    op.create_table('bill_titles',
    sa.Column('id', sa.UUID(), autoincrement=False, nullable=False),
    sa.Column('source', sa.VARCHAR(length=64), autoincrement=False, nullable=False),
    sa.Column('source_id', sa.VARCHAR(length=128), autoincrement=False, nullable=False),
    sa.Column('bill_id', sa.UUID(), autoincrement=False, nullable=False),
    sa.Column('title_text', sa.TEXT(), autoincrement=False, nullable=False),
    sa.Column('title_type', sa.VARCHAR(length=32), autoincrement=False, nullable=False),
    sa.Column('as_of_action', sa.VARCHAR(length=64), autoincrement=False, nullable=True),
    sa.Column('language_code', sa.VARCHAR(length=8), autoincrement=False, nullable=True),
    sa.Column('amendment_id', sa.UUID(), autoincrement=False, nullable=True),
    sa.Column('effective_at', postgresql.TIMESTAMP(timezone=True), autoincrement=False, nullable=True),
    sa.Column('replaced_at', postgresql.TIMESTAMP(timezone=True), autoincrement=False, nullable=True),
    sa.Column('is_current', sa.BOOLEAN(), autoincrement=False, nullable=False),
    sa.Column('created_at', postgresql.TIMESTAMP(timezone=True), server_default=sa.text('now()'), autoincrement=False, nullable=False),
    sa.Column('updated_at', postgresql.TIMESTAMP(timezone=True), server_default=sa.text('now()'), autoincrement=False, nullable=False),
    sa.Column('chamber_id', sa.UUID(), autoincrement=False, nullable=True),
    sa.ForeignKeyConstraint(['amendment_id'], ['canonical.amendments.id'], name=op.f('bill_titles_amendment_id_fkey'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['bill_id'], ['canonical.bills.id'], name=op.f('bill_titles_bill_id_fkey'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['chamber_id'], ['canonical.organizations.id'], name=op.f('fk_bill_titles_chamber_id'), ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id', name=op.f('bill_titles_pkey')),
    sa.UniqueConstraint('source', 'source_id', name=op.f('uq_bill_titles_natural_key'), postgresql_include=[], postgresql_nulls_not_distinct=False),
    schema='canonical'
    )
    op.create_index(op.f('ix_canonical_bill_titles_chamber_id'), 'bill_titles', ['chamber_id'], unique=False, schema='canonical')
    op.create_index(op.f('ix_canonical_bill_titles_bill_id'), 'bill_titles', ['bill_id'], unique=False, schema='canonical')
    op.create_index(op.f('ix_canonical_bill_titles_amendment_id'), 'bill_titles', ['amendment_id'], unique=False, schema='canonical')
    op.create_table('bill_version_links',
    sa.Column('id', sa.UUID(), autoincrement=False, nullable=False),
    sa.Column('source', sa.VARCHAR(length=64), autoincrement=False, nullable=False),
    sa.Column('source_id', sa.VARCHAR(length=128), autoincrement=False, nullable=False),
    sa.Column('bill_version_id', sa.UUID(), autoincrement=False, nullable=False),
    sa.Column('url', sa.VARCHAR(length=2048), autoincrement=False, nullable=False),
    sa.Column('mime_type', sa.VARCHAR(length=128), autoincrement=False, nullable=True),
    sa.Column('kind', sa.VARCHAR(length=32), autoincrement=False, nullable=False),
    sa.Column('title', sa.VARCHAR(length=256), autoincrement=False, nullable=True),
    sa.Column('created_at', postgresql.TIMESTAMP(timezone=True), server_default=sa.text('now()'), autoincrement=False, nullable=False),
    sa.Column('updated_at', postgresql.TIMESTAMP(timezone=True), server_default=sa.text('now()'), autoincrement=False, nullable=False),
    sa.ForeignKeyConstraint(['bill_version_id'], ['canonical.bill_versions.id'], name=op.f('bill_version_links_bill_version_id_fkey'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('bill_version_links_pkey')),
    sa.UniqueConstraint('source', 'source_id', name=op.f('uq_bill_version_links_natural_key'), postgresql_include=[], postgresql_nulls_not_distinct=False),
    schema='canonical'
    )
    op.create_index(op.f('ix_canonical_bill_version_links_bill_version_id'), 'bill_version_links', ['bill_version_id'], unique=False, schema='canonical')
    op.create_table('bill_relationship_types',
    sa.Column('id', sa.UUID(), autoincrement=False, nullable=False),
    sa.Column('code', sa.VARCHAR(length=32), autoincrement=False, nullable=False),
    sa.Column('display_name', sa.VARCHAR(length=64), autoincrement=False, nullable=False),
    sa.Column('symmetric', sa.BOOLEAN(), server_default=sa.text('false'), autoincrement=False, nullable=False),
    sa.Column('description', sa.TEXT(), autoincrement=False, nullable=True),
    sa.Column('created_at', postgresql.TIMESTAMP(timezone=True), server_default=sa.text('now()'), autoincrement=False, nullable=False),
    sa.Column('updated_at', postgresql.TIMESTAMP(timezone=True), server_default=sa.text('now()'), autoincrement=False, nullable=False),
    sa.Column('jurisdiction_id', sa.UUID(), autoincrement=False, nullable=False),
    sa.ForeignKeyConstraint(['jurisdiction_id'], ['clearinghouse_core.jurisdictions.id'], name=op.f('fk_bill_relationship_types_jurisdiction_id'), ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id', name=op.f('bill_relationship_types_pkey')),
    sa.UniqueConstraint('jurisdiction_id', 'code', name=op.f('uq_bill_relationship_types_jurisdiction_code'), postgresql_include=[], postgresql_nulls_not_distinct=False),
    schema='canonical'
    )
    op.create_index(op.f('ix_canonical_bill_relationship_types_jurisdiction_id'), 'bill_relationship_types', ['jurisdiction_id'], unique=False, schema='canonical')
    op.create_table('bill_relationships',
    sa.Column('id', sa.UUID(), autoincrement=False, nullable=False),
    sa.Column('source', sa.VARCHAR(length=64), autoincrement=False, nullable=False),
    sa.Column('source_id', sa.VARCHAR(length=128), autoincrement=False, nullable=False),
    sa.Column('from_bill_id', sa.UUID(), autoincrement=False, nullable=False),
    sa.Column('to_bill_id', sa.UUID(), autoincrement=False, nullable=False),
    sa.Column('notes', sa.TEXT(), autoincrement=False, nullable=True),
    sa.Column('updated_at', postgresql.TIMESTAMP(timezone=True), server_default=sa.text('now()'), autoincrement=False, nullable=False),
    sa.Column('created_at', postgresql.TIMESTAMP(timezone=True), server_default=sa.text('now()'), autoincrement=False, nullable=False),
    sa.Column('relationship_type_id', sa.UUID(), autoincrement=False, nullable=False),
    sa.ForeignKeyConstraint(['from_bill_id'], ['canonical.bills.id'], name=op.f('bill_relationships_from_bill_id_fkey'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['relationship_type_id'], ['canonical.bill_relationship_types.id'], name=op.f('fk_bill_relationships_relationship_type_id'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['to_bill_id'], ['canonical.bills.id'], name=op.f('bill_relationships_to_bill_id_fkey'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('bill_relationships_pkey')),
    sa.UniqueConstraint('from_bill_id', 'to_bill_id', 'relationship_type_id', name=op.f('uq_bill_relationships_pair_type'), postgresql_include=[], postgresql_nulls_not_distinct=False),
    sa.UniqueConstraint('source', 'source_id', name=op.f('uq_bill_relationships_natural_key'), postgresql_include=[], postgresql_nulls_not_distinct=False),
    schema='canonical'
    )
    op.create_index(op.f('ix_canonical_bill_relationships_to_bill_id'), 'bill_relationships', ['to_bill_id'], unique=False, schema='canonical')
    op.create_index(op.f('ix_canonical_bill_relationships_relationship_type_id'), 'bill_relationships', ['relationship_type_id'], unique=False, schema='canonical')
    op.create_index(op.f('ix_canonical_bill_relationships_from_bill_id'), 'bill_relationships', ['from_bill_id'], unique=False, schema='canonical')
    op.create_table('bill_events',
    sa.Column('id', sa.UUID(), autoincrement=False, nullable=False),
    sa.Column('source', sa.VARCHAR(length=64), autoincrement=False, nullable=False),
    sa.Column('source_id', sa.VARCHAR(length=128), autoincrement=False, nullable=False),
    sa.Column('bill_id', sa.UUID(), autoincrement=False, nullable=True),
    sa.Column('organization_id', sa.UUID(), autoincrement=False, nullable=True),
    sa.Column('event_type', sa.VARCHAR(length=32), autoincrement=False, nullable=False),
    sa.Column('scheduled_at', postgresql.TIMESTAMP(timezone=True), autoincrement=False, nullable=False),
    sa.Column('ended_at', postgresql.TIMESTAMP(timezone=True), autoincrement=False, nullable=True),
    sa.Column('location', sa.VARCHAR(length=256), autoincrement=False, nullable=True),
    sa.Column('status', sa.VARCHAR(length=32), autoincrement=False, nullable=False),
    sa.Column('description', sa.TEXT(), autoincrement=False, nullable=True),
    sa.Column('updated_at', postgresql.TIMESTAMP(timezone=True), server_default=sa.text('now()'), autoincrement=False, nullable=False),
    sa.Column('created_at', postgresql.TIMESTAMP(timezone=True), server_default=sa.text('now()'), autoincrement=False, nullable=False),
    sa.ForeignKeyConstraint(['bill_id'], ['canonical.bills.id'], name=op.f('bill_events_bill_id_fkey'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['organization_id'], ['canonical.organizations.id'], name=op.f('bill_events_organization_id_fkey'), ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id', name=op.f('bill_events_pkey')),
    sa.UniqueConstraint('source', 'source_id', name=op.f('uq_bill_events_natural_key'), postgresql_include=[], postgresql_nulls_not_distinct=False),
    schema='canonical'
    )
    op.create_index(op.f('ix_canonical_bill_events_organization_id'), 'bill_events', ['organization_id'], unique=False, schema='canonical')
    op.create_index(op.f('ix_canonical_bill_events_bill_id'), 'bill_events', ['bill_id'], unique=False, schema='canonical')
    op.create_table('bill_action_classifications',
    sa.Column('id', sa.UUID(), autoincrement=False, nullable=False),
    sa.Column('source', sa.VARCHAR(length=64), autoincrement=False, nullable=False),
    sa.Column('source_id', sa.VARCHAR(length=128), autoincrement=False, nullable=False),
    sa.Column('bill_action_id', sa.UUID(), autoincrement=False, nullable=False),
    sa.Column('classification', sa.VARCHAR(length=64), autoincrement=False, nullable=False),
    sa.Column('updated_at', postgresql.TIMESTAMP(timezone=True), server_default=sa.text('now()'), autoincrement=False, nullable=False),
    sa.Column('created_at', postgresql.TIMESTAMP(timezone=True), server_default=sa.text('now()'), autoincrement=False, nullable=False),
    sa.ForeignKeyConstraint(['bill_action_id'], ['canonical.bill_actions.id'], name=op.f('bill_action_classifications_bill_action_id_fkey'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('bill_action_classifications_pkey')),
    sa.UniqueConstraint('bill_action_id', 'classification', name=op.f('uq_bill_action_classifications_action_class'), postgresql_include=[], postgresql_nulls_not_distinct=False),
    sa.UniqueConstraint('source', 'source_id', name=op.f('uq_bill_action_classifications_natural_key'), postgresql_include=[], postgresql_nulls_not_distinct=False),
    schema='canonical'
    )
    op.create_index(op.f('ix_canonical_bill_action_classifications_bill_action_id'), 'bill_action_classifications', ['bill_action_id'], unique=False, schema='canonical')
    op.create_table('bill_subjects',
    sa.Column('id', sa.UUID(), autoincrement=False, nullable=False),
    sa.Column('source', sa.VARCHAR(length=64), autoincrement=False, nullable=False),
    sa.Column('source_id', sa.VARCHAR(length=128), autoincrement=False, nullable=False),
    sa.Column('bill_id', sa.UUID(), autoincrement=False, nullable=False),
    sa.Column('subject', sa.VARCHAR(length=128), autoincrement=False, nullable=False),
    sa.Column('is_primary', sa.BOOLEAN(), autoincrement=False, nullable=False),
    sa.Column('updated_at', postgresql.TIMESTAMP(timezone=True), server_default=sa.text('now()'), autoincrement=False, nullable=False),
    sa.Column('created_at', postgresql.TIMESTAMP(timezone=True), server_default=sa.text('now()'), autoincrement=False, nullable=False),
    sa.ForeignKeyConstraint(['bill_id'], ['canonical.bills.id'], name=op.f('bill_subjects_bill_id_fkey'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('bill_subjects_pkey')),
    sa.UniqueConstraint('bill_id', 'subject', name=op.f('uq_bill_subjects_bill_subject'), postgresql_include=[], postgresql_nulls_not_distinct=False),
    sa.UniqueConstraint('source', 'source_id', name=op.f('uq_bill_subjects_natural_key'), postgresql_include=[], postgresql_nulls_not_distinct=False),
    schema='canonical'
    )
    op.create_index(op.f('ix_canonical_bill_subjects_bill_id'), 'bill_subjects', ['bill_id'], unique=False, schema='canonical')
    op.create_table('lobbying_positions',
    sa.Column('id', sa.UUID(), autoincrement=False, nullable=False),
    sa.Column('source', sa.VARCHAR(length=64), autoincrement=False, nullable=False),
    sa.Column('source_id', sa.VARCHAR(length=128), autoincrement=False, nullable=False),
    sa.Column('lobbying_activity_id', sa.UUID(), autoincrement=False, nullable=False),
    sa.Column('bill_id', sa.UUID(), autoincrement=False, nullable=True),
    sa.Column('bill_reference_raw', sa.VARCHAR(length=128), autoincrement=False, nullable=True),
    sa.Column('position', sa.VARCHAR(length=16), autoincrement=False, nullable=False),
    sa.Column('updated_at', postgresql.TIMESTAMP(timezone=True), server_default=sa.text('now()'), autoincrement=False, nullable=False),
    sa.Column('created_at', postgresql.TIMESTAMP(timezone=True), server_default=sa.text('now()'), autoincrement=False, nullable=False),
    sa.Column('jurisdiction_id', sa.UUID(), autoincrement=False, nullable=False),
    sa.ForeignKeyConstraint(['bill_id'], ['canonical.bills.id'], name=op.f('lobbying_positions_bill_id_fkey'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['jurisdiction_id'], ['clearinghouse_core.jurisdictions.id'], name=op.f('fk_lobbying_positions_jurisdiction_id'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['lobbying_activity_id'], ['canonical.lobbying_activities.id'], name=op.f('lobbying_positions_lobbying_activity_id_fkey'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('lobbying_positions_pkey')),
    sa.UniqueConstraint('jurisdiction_id', 'source', 'source_id', name=op.f('uq_lobbying_positions_natural_key'), postgresql_include=[], postgresql_nulls_not_distinct=False),
    schema='canonical'
    )
    op.create_index(op.f('ix_canonical_lobbying_positions_lobbying_activity_id'), 'lobbying_positions', ['lobbying_activity_id'], unique=False, schema='canonical')
    op.create_index(op.f('ix_canonical_lobbying_positions_jurisdiction_id'), 'lobbying_positions', ['jurisdiction_id'], unique=False, schema='canonical')
    op.create_index(op.f('ix_canonical_lobbying_positions_bill_id'), 'lobbying_positions', ['bill_id'], unique=False, schema='canonical')
    op.create_table('integrity_sweep_state',
    sa.Column('id', sa.UUID(), autoincrement=False, nullable=False),
    sa.Column('scope', sa.VARCHAR(length=64), autoincrement=False, nullable=False),
    sa.Column('cursor', sa.UUID(), autoincrement=False, nullable=True),
    sa.Column('created_at', postgresql.TIMESTAMP(timezone=True), server_default=sa.text('now()'), autoincrement=False, nullable=False),
    sa.Column('updated_at', postgresql.TIMESTAMP(timezone=True), server_default=sa.text('now()'), autoincrement=False, nullable=False),
    sa.PrimaryKeyConstraint('id', name=op.f('integrity_sweep_state_pkey')),
    sa.UniqueConstraint('scope', name=op.f('uq_integrity_sweep_state_scope'), postgresql_include=[], postgresql_nulls_not_distinct=False),
    schema='clearinghouse_core'
    )
    op.create_table('bill_statutory_citations',
    sa.Column('id', sa.UUID(), autoincrement=False, nullable=False),
    sa.Column('source', sa.VARCHAR(length=64), autoincrement=False, nullable=False),
    sa.Column('source_id', sa.VARCHAR(length=128), autoincrement=False, nullable=False),
    sa.Column('bill_version_id', sa.UUID(), autoincrement=False, nullable=False),
    sa.Column('statute_section_id', sa.UUID(), autoincrement=False, nullable=True),
    sa.Column('raw_text', sa.VARCHAR(length=256), autoincrement=False, nullable=False),
    sa.Column('text_offset_start', sa.INTEGER(), autoincrement=False, nullable=True),
    sa.Column('text_offset_end', sa.INTEGER(), autoincrement=False, nullable=True),
    sa.Column('created_at', postgresql.TIMESTAMP(timezone=True), server_default=sa.text('now()'), autoincrement=False, nullable=False),
    sa.Column('updated_at', postgresql.TIMESTAMP(timezone=True), server_default=sa.text('now()'), autoincrement=False, nullable=False),
    sa.ForeignKeyConstraint(['bill_version_id'], ['canonical.bill_versions.id'], name=op.f('bill_statutory_citations_bill_version_id_fkey'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['statute_section_id'], ['canonical.statute_sections.id'], name=op.f('bill_statutory_citations_statute_section_id_fkey'), ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id', name=op.f('bill_statutory_citations_pkey')),
    sa.UniqueConstraint('source', 'source_id', name=op.f('uq_bill_statutory_citations_natural_key'), postgresql_include=[], postgresql_nulls_not_distinct=False),
    schema='canonical'
    )
    op.create_index(op.f('ix_canonical_bill_statutory_citations_statute_section_id'), 'bill_statutory_citations', ['statute_section_id'], unique=False, schema='canonical')
    op.create_index(op.f('ix_canonical_bill_statutory_citations_bill_version_id'), 'bill_statutory_citations', ['bill_version_id'], unique=False, schema='canonical')
    op.create_table('citations',
    sa.Column('id', sa.UUID(), autoincrement=False, nullable=False),
    sa.Column('entity_type', sa.VARCHAR(length=64), autoincrement=False, nullable=False),
    sa.Column('entity_id', sa.UUID(), autoincrement=False, nullable=False),
    sa.Column('fetch_event_id', sa.UUID(), autoincrement=False, nullable=False),
    sa.Column('field_path', sa.VARCHAR(length=128), autoincrement=False, nullable=True),
    sa.Column('confidence', sa.DOUBLE_PRECISION(precision=53), autoincrement=False, nullable=False),
    sa.Column('asserted_at', postgresql.TIMESTAMP(timezone=True), autoincrement=False, nullable=False),
    sa.Column('created_at', postgresql.TIMESTAMP(timezone=True), server_default=sa.text('now()'), autoincrement=False, nullable=False),
    sa.ForeignKeyConstraint(['fetch_event_id'], ['clearinghouse_core.fetch_events.id'], name=op.f('citations_fetch_event_id_fkey'), ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id', name=op.f('citations_pkey')),
    schema='clearinghouse_core'
    )
    op.create_index(op.f('ix_clearinghouse_core_citations_entity_type'), 'citations', ['entity_type'], unique=False, schema='clearinghouse_core')
    op.create_index(op.f('ix_clearinghouse_core_citations_entity_id'), 'citations', ['entity_id'], unique=False, schema='clearinghouse_core')
    op.create_table('document_identifiers',
    sa.Column('id', sa.UUID(), autoincrement=False, nullable=False),
    sa.Column('source', sa.VARCHAR(length=64), autoincrement=False, nullable=False),
    sa.Column('source_id', sa.VARCHAR(length=128), autoincrement=False, nullable=False),
    sa.Column('entity_type', sa.VARCHAR(length=32), autoincrement=False, nullable=False),
    sa.Column('entity_id', sa.UUID(), autoincrement=False, nullable=False),
    sa.Column('scheme', sa.VARCHAR(length=64), autoincrement=False, nullable=False),
    sa.Column('value', sa.VARCHAR(length=256), autoincrement=False, nullable=False),
    sa.Column('parsed_components', postgresql.JSONB(astext_type=sa.Text()), autoincrement=False, nullable=True),
    sa.Column('verified_at', postgresql.TIMESTAMP(timezone=True), autoincrement=False, nullable=True),
    sa.Column('created_at', postgresql.TIMESTAMP(timezone=True), server_default=sa.text('now()'), autoincrement=False, nullable=False),
    sa.Column('updated_at', postgresql.TIMESTAMP(timezone=True), server_default=sa.text('now()'), autoincrement=False, nullable=False),
    sa.Column('jurisdiction_id', sa.UUID(), autoincrement=False, nullable=False),
    sa.ForeignKeyConstraint(['jurisdiction_id'], ['clearinghouse_core.jurisdictions.id'], name=op.f('fk_document_identifiers_jurisdiction_id'), ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id', name=op.f('document_identifiers_pkey')),
    sa.UniqueConstraint('entity_type', 'entity_id', 'scheme', name=op.f('uq_document_identifiers_entity_scheme'), postgresql_include=[], postgresql_nulls_not_distinct=False),
    sa.UniqueConstraint('jurisdiction_id', 'entity_type', 'scheme', 'value', name=op.f('uq_document_identifiers_jurisdiction_entity_scheme_value'), postgresql_include=[], postgresql_nulls_not_distinct=False),
    sa.UniqueConstraint('jurisdiction_id', 'source', 'source_id', name=op.f('uq_document_identifiers_natural_key'), postgresql_include=[], postgresql_nulls_not_distinct=False),
    schema='clearinghouse_core'
    )
    op.create_index(op.f('ix_clearinghouse_core_document_identifiers_jurisdiction_id'), 'document_identifiers', ['jurisdiction_id'], unique=False, schema='clearinghouse_core')
    op.create_index(op.f('ix_clearinghouse_core_document_identifiers_entity_type'), 'document_identifiers', ['entity_type'], unique=False, schema='clearinghouse_core')
    op.create_index(op.f('ix_clearinghouse_core_document_identifiers_entity_id'), 'document_identifiers', ['entity_id'], unique=False, schema='clearinghouse_core')
    op.create_foreign_key(op.f('fk_bills_current_version_id'), 'bills', 'bill_versions', ['current_version_id'], ['id'], source_schema='canonical', referent_schema='canonical', ondelete='SET NULL')
    op.add_column('source_coverage', sa.Column('evidence_citation_id', sa.UUID(), autoincrement=False, nullable=True), schema='clearinghouse_core')
    op.create_foreign_key(op.f('source_coverage_evidence_citation_id_fkey'), 'source_coverage', 'citations', ['evidence_citation_id'], ['id'], source_schema='clearinghouse_core', referent_schema='clearinghouse_core', ondelete='SET NULL')
    op.add_column('jurisdictions', sa.Column('pm_jurisdiction_id', sa.UUID(), autoincrement=False, nullable=True), schema='clearinghouse_core')
    op.create_index(op.f('ix_clearinghouse_core_jurisdictions_pm_jurisdiction_id'), 'jurisdictions', ['pm_jurisdiction_id'], unique=False, schema='clearinghouse_core')
    # ### end Alembic commands ###
