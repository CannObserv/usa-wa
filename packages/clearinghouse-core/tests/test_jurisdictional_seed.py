"""Sync unit tests for the Jurisdictional IA migration's seed shape.

Validates the migration's lookup-vocab constants against
``initial_jurisdictions.json`` without running alembic. Catches the "added a
slug/code to the JSON but forgot to seed it in the migration lookup" regression
class flagged in code-review round 2, finding #22.

The integration counterpart that actually runs ``alembic upgrade head`` lives
in :mod:`test_jurisdictional_seed_integration` so the heavy async +
sqlalchemy + alembic imports don't load on the default test tier.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
MIGRATION_PATH = REPO_ROOT / "alembic/versions/2026_06_03_jurisdictional_ia_refactor.py"
JSON_SEED_PATH = (
    REPO_ROOT
    / "packages/usa-wa-adapter-legislature/src/usa_wa_adapter_legislature"
    / "data/initial_jurisdictions.json"
)


def _load_migration_module():
    """Import the migration as a module for constant introspection.

    The migration's body uses ``op.execute`` which requires an active alembic
    context, but we only read module-level constants here (``JURISDICTION_TYPES``
    etc.). The migration MUST NOT execute ``op.*`` at module scope or this
    loader will crash — guard against silent breakage by wrapping the exec in a
    try/except that produces an actionable error message.
    """
    spec = importlib.util.spec_from_file_location("_ia_migration", MIGRATION_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(module)
    except Exception as exc:
        raise RuntimeError(
            f"Failed to load {MIGRATION_PATH.name} as a module for introspection. "
            "This test relies on the migration body not executing op.* at module "
            "scope (only inside upgrade()/downgrade()). If a recent change moved "
            "op.execute or similar into module scope, restructure the migration "
            "or rewrite this test to parse constants differently."
        ) from exc
    return module


def test_seed_vocab_covers_initial_jurisdictions_json():
    """Every type/relationship-type referenced in the JSON seed exists in the
    migration's lookup vocab, and the vocab sizes match the design (16 + 11).
    """
    migration = _load_migration_module()
    type_slugs = {slug for slug, _ in migration.JURISDICTION_TYPES}
    rel_codes = {code for code, _, _, _ in migration.JURISDICTION_RELATIONSHIP_TYPES}

    assert len(migration.JURISDICTION_TYPES) == 16, (
        "JURISDICTION_TYPES must seed exactly 16 rows (mirrors PM's lookup; see design spec §1)"
    )
    assert len(migration.JURISDICTION_RELATIONSHIP_TYPES) == 11, (
        "JURISDICTION_RELATIONSHIP_TYPES must seed exactly 11 codes (PM dropped "
        "exercises_concurrent_jurisdiction from MVP per Phase 1)"
    )

    with JSON_SEED_PATH.open() as f:
        seed = json.load(f)

    json_types = {j["type"] for j in seed["jurisdictions"]}
    missing_types = json_types - type_slugs
    assert not missing_types, (
        f"initial_jurisdictions.json uses jurisdiction types not in "
        f"JURISDICTION_TYPES: {sorted(missing_types)}. Add them to the migration "
        "lookup or update the seed."
    )

    json_rel_codes = {r["relationship_type"] for r in seed["relationships"]}
    missing_codes = json_rel_codes - rel_codes
    assert not missing_codes, (
        f"initial_jurisdictions.json uses relationship_type codes not in "
        f"JURISDICTION_RELATIONSHIP_TYPES: {sorted(missing_codes)}. Add them to "
        "the migration lookup or update the seed."
    )


def test_seed_jurisdiction_relationships_reference_existing_slugs():
    """Every relationship's subject_slug + object_slug must appear in the
    jurisdictions list of the same seed file (the alembic seed inserts use
    slug-keyed SELECT subqueries that produce NULL FKs otherwise).
    """
    with JSON_SEED_PATH.open() as f:
        seed = json.load(f)
    all_slugs = {j["slug"] for j in seed["jurisdictions"]}

    dangling = []
    for r in seed["relationships"]:
        for side in ("subject_slug", "object_slug"):
            if r[side] not in all_slugs:
                dangling.append((r[side], side, r))
    assert not dangling, (
        f"initial_jurisdictions.json has relationships referencing slugs not in "
        f"its jurisdictions list: {dangling[:3]} (showing first 3)."
    )
