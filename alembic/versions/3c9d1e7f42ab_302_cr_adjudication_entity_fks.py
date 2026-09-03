"""302 CR: FK the adjudication ledger's entity references

The adjudication ledger is the only path that moves or merges identity; its
``subject_entity_id``/``target_entity_id`` could reference entities that do not
exist (a typo'd merge target recorded as a valid-looking decision). Entities
are never deleted, so the constraint costs nothing (#302 CR 23).

Revision ID: 3c9d1e7f42ab
Revises: 97e40a1f61ab
Create Date: 2026-09-03 13:55:00.000000

"""

from collections.abc import Sequence

from alembic import op

revision: str = "3c9d1e7f42ab"
down_revision: str | Sequence[str] | None = "97e40a1f61ab"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add the two FKs. Replayable from empty: the tables exist by 97e40a1f61ab."""
    op.create_foreign_key(
        "fk_registry_adjudications_subject_entity",
        "adjudications",
        "entities",
        ["subject_entity_id"],
        ["id"],
        source_schema="registry",
        referent_schema="registry",
    )
    op.create_foreign_key(
        "fk_registry_adjudications_target_entity",
        "adjudications",
        "entities",
        ["target_entity_id"],
        ["id"],
        source_schema="registry",
        referent_schema="registry",
    )


def downgrade() -> None:
    """Drop the two FKs."""
    op.drop_constraint(
        "fk_registry_adjudications_subject_entity",
        "adjudications",
        schema="registry",
        type_="foreignkey",
    )
    op.drop_constraint(
        "fk_registry_adjudications_target_entity",
        "adjudications",
        schema="registry",
        type_="foreignkey",
    )
