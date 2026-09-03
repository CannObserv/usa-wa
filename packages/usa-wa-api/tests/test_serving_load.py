"""The serving loader (#313): published datasets → the disposable `serving` schema.

The deployment becomes the first consumer of its own datapackage contract, so
these pin the two things that makes true: the loader reads what the *catalog*
says is current (not a path it guesses), and it refuses a datapackage whose
fields no longer match the table it would load — a contract check with teeth,
rather than a silent partial load.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest
from sqlalchemy import func, select

from usa_wa_api.serving.load import (
    ContractMismatch,
    catalog_entries,
    coerce_row,
    create_serving_tables,
    dataset_rows,
    ensure_serving_schema,
    load_serving,
    verify_contract,
)
from usa_wa_api.serving.schema import SERVING_TABLES, Assignment, Person


@pytest.fixture
async def serving_schema(db_session):
    """Create the disposable schema for one test.

    It is created here rather than by a migration on purpose: this tier owns no
    state worth preserving, so `create_all` in the loader's own job is the whole
    lifecycle (see `usa_wa_api.serving.schema`).
    """
    await ensure_serving_schema(db_session)
    await create_serving_tables(db_session)
    return db_session


FIELDS = {
    "persons": [
        {"name": "entity_id", "type": "string"},
        {"name": "name_full", "type": "string"},
        {"name": "name_source", "type": "string"},
    ],
    "assignments": [
        {"name": "entity_id", "type": "string"},
        {"name": "member_id", "type": "string"},
        {"name": "source", "type": "string"},
        {"name": "role_key", "type": "string"},
        {"name": "span_kind", "type": "string"},
        {"name": "span_discriminator", "type": "string"},
        {"name": "span_start_biennium", "type": "string"},
        {"name": "span_end_biennium", "type": "string"},
        {"name": "valid_from", "type": "date"},
        {"name": "valid_to", "type": "date"},
        {"name": "is_active", "type": "boolean"},
    ],
}


def _publish(root: Path, name: str, version: str, rows: list[dict], fields=None) -> None:
    """Write one dataset version the way the publisher does, and list it."""
    fields = fields if fields is not None else FIELDS[name]
    version_dir = root / name / version
    version_dir.mkdir(parents=True)
    header = [field["name"] for field in fields]
    lines = [",".join(header)]
    lines += [
        ",".join("" if row.get(k) is None else str(row.get(k)) for k in header) for row in rows
    ]
    (version_dir / "data.csv").write_text("\n".join(lines) + "\n")
    (version_dir / "datapackage.json").write_text(
        json.dumps(
            {
                "name": name,
                "version": version,
                "tier": "conformed",
                "resources": [{"name": name, "rows": len(rows), "schema": {"fields": fields}}],
            }
        )
    )
    catalog_path = root / "catalog.json"
    catalog = (
        json.loads(catalog_path.read_text())
        if catalog_path.is_file()
        else {"generated_at": "2026-09-03T00:00:00.000000Z", "datasets": []}
    )
    catalog["datasets"] = [e for e in catalog["datasets"] if e["name"] != name] + [
        {
            "name": name,
            "tier": "conformed",
            "latest_version": version,
            "rows": len(rows),
            "generated_at": "2026-09-03T00:00:00.000000Z",
        }
    ]
    catalog_path.write_text(json.dumps(catalog))


def test_the_catalog_names_the_version_the_loader_reads(tmp_path) -> None:
    """The loader must never guess a path. `catalog.json` is the published
    contract's own index of what is current, so an older version dir sitting
    beside the newest is simply not loaded — which is what makes the immutable
    version tree safe to keep forever."""
    _publish(tmp_path, "persons", "v1", [{"entity_id": "01A", "name_full": "Old"}])
    _publish(tmp_path, "persons", "v2", [{"entity_id": "01A", "name_full": "New"}])
    entries = catalog_entries(tmp_path)
    assert entries["persons"]["latest_version"] == "v2"
    rows = dataset_rows(tmp_path, "persons", "v2")
    assert rows[0]["name_full"] == "New"


def test_an_unpublished_catalog_is_empty_not_an_error(tmp_path) -> None:
    """Absence is the finding, the #180 posture: a box that has never published
    reports nothing to load rather than raising."""
    assert catalog_entries(tmp_path) == {}


def test_the_contract_is_verified_against_the_table(tmp_path) -> None:
    """The point of the flip is that the API consumes its own datapackage. A
    field the table has no column for — or a column the datapackage stopped
    publishing — is a contract break, and loading anyway would leave a table
    that silently disagrees with what `/datasets` serves."""
    assert verify_contract("persons", FIELDS["persons"], SERVING_TABLES["persons"]) == []


def test_a_datapackage_that_dropped_a_field_refuses_the_load(tmp_path) -> None:
    dropped = [f for f in FIELDS["persons"] if f["name"] != "name_source"]
    problems = verify_contract("persons", dropped, SERVING_TABLES["persons"])
    assert problems and "name_source" in problems[0]


def test_a_datapackage_that_added_a_field_refuses_the_load(tmp_path) -> None:
    added = [*FIELDS["persons"], {"name": "nickname", "type": "string"}]
    problems = verify_contract("persons", added, SERVING_TABLES["persons"])
    assert problems and "nickname" in problems[0]


def test_csv_strings_become_the_types_the_datapackage_declares() -> None:
    """CSV has one type. The datapackage says what each column means, so the
    coercion is contract-driven rather than guessed per column name."""
    row = coerce_row(
        {"valid_from": "2021-01-01", "valid_to": "", "is_active": "true", "member_id": "31526"},
        {
            "valid_from": "date",
            "valid_to": "date",
            "is_active": "boolean",
            "member_id": "string",
        },
    )
    assert row["valid_from"] == date(2021, 1, 1)
    assert row["valid_to"] is None
    assert row["is_active"] is True
    assert row["member_id"] == "31526"


def test_an_empty_string_is_null_not_an_empty_value() -> None:
    """Every published column round-trips absence as an empty CSV cell, so the
    loader must not turn a missing name into the empty string — `/api/v1` would
    then answer "" where the dataset says nothing is known."""
    row = coerce_row(
        {"name_source": "", "name_full": "Dana"}, {"name_source": "string", "name_full": "string"}
    )
    assert row["name_source"] is None
    assert row["name_full"] == "Dana"


@pytest.mark.db
async def test_load_replaces_the_whole_snapshot(serving_schema, db_session, tmp_path) -> None:
    """A dataset version is a snapshot, so the load is a replacement, not a
    merge: retraction-as-absence means a row the publisher stopped asserting
    must disappear here too."""
    _publish(tmp_path, "persons", "v1", [{"entity_id": "01A", "name_full": "Dana"}])
    counters = await load_serving(db_session, tmp_path, datasets=("persons",))
    assert counters["persons"] == 1

    _publish(tmp_path, "persons", "v2", [{"entity_id": "01B", "name_full": "Rae"}])
    counters = await load_serving(db_session, tmp_path, datasets=("persons",))
    assert counters["persons"] == 1
    rows = (await db_session.execute(select(Person.entity_id))).scalars().all()
    assert rows == ["01B"]


@pytest.mark.db
async def test_a_contract_break_loads_nothing_at_all(serving_schema, db_session, tmp_path) -> None:
    """Refusing one dataset but committing the others would leave the serving
    schema internally inconsistent — assignments pointing at persons that were
    not refreshed. The whole load is one transaction and one decision."""
    _publish(tmp_path, "persons", "v1", [{"entity_id": "01A", "name_full": "Dana"}])
    await load_serving(db_session, tmp_path, datasets=("persons",))

    broken = [*FIELDS["persons"], {"name": "nickname", "type": "string"}]
    _publish(tmp_path, "persons", "v2", [{"entity_id": "01B", "name_full": "Rae"}], fields=broken)
    with pytest.raises(ContractMismatch, match="nickname"):
        await load_serving(db_session, tmp_path, datasets=("persons",))
    # the previous snapshot still stands
    rows = (await db_session.execute(select(Person.entity_id))).scalars().all()
    assert rows == ["01A"]


@pytest.mark.db
async def test_typed_columns_survive_the_round_trip(serving_schema, db_session, tmp_path) -> None:
    """The dates and the boolean are what `/api/v1`'s `as_of` and `is_active`
    filters run on, so they have to arrive as dates and a boolean — not as the
    strings the CSV carries."""
    _publish(
        tmp_path,
        "assignments",
        "v1",
        [
            {
                "entity_id": "01A",
                "member_id": "31526",
                "source": "usa_wa_legislature",
                "role_key": "seat:senate:ld-14",
                "span_kind": "chamber-senate",
                "span_discriminator": "14",
                "span_start_biennium": "2021-22",
                "span_end_biennium": "2025-26",
                "valid_from": "2021-01-01",
                "valid_to": None,
                "is_active": "true",
            }
        ],
    )
    await load_serving(db_session, tmp_path, datasets=("assignments",))
    row = (await db_session.execute(select(Assignment))).scalars().one()
    assert row.valid_from == date(2021, 1, 1)
    assert row.valid_to is None
    assert row.is_active is True


@pytest.mark.db
async def test_a_dataset_the_catalog_does_not_list_refuses(
    serving_schema, db_session, tmp_path
) -> None:
    """The serving schema is rebuildable from `published/` alone — so a dataset
    the API needs and the catalog does not carry is a refusal, not an empty
    table that would answer 200 with nothing in it."""
    _publish(tmp_path, "persons", "v1", [{"entity_id": "01A", "name_full": "Dana"}])
    with pytest.raises(ContractMismatch, match="assignments"):
        await load_serving(db_session, tmp_path, datasets=("persons", "assignments"))
    assert (await db_session.scalar(select(func.count()).select_from(Person.__table__))) == 0
