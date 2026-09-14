"""The dataset publisher (#311): built duckdb → immutable versions + catalog."""

import csv
import io
import json

import duckdb
import pytest

from usa_wa_pipeline import publish as publish_mod
from usa_wa_pipeline.publish import (
    CSV_DIALECT,
    PUBLISHED_DATASETS,
    PublishRefused,
    publish,
)


@pytest.fixture
def built_db(tmp_path):
    db = tmp_path / "pipeline.duckdb"
    con = duckdb.connect(str(db))
    con.execute("create table persons as select '01A' as entity_id, 'Dana' as name_full")
    con.execute(
        "create table person_crosswalk as "
        "select '01A' as entity_id, 'usa_wa_legislature:1' as natural_key"
    )
    con.close()
    return db


def _manifest(tmp_path):
    path = tmp_path / "manifest.json"
    path.write_text(
        json.dumps(
            {
                "nodes": {
                    "model.usa_wa_pipeline.persons": {
                        "depends_on": {"nodes": ["model.usa_wa_pipeline.person_crosswalk"]}
                    },
                    "model.usa_wa_pipeline.person_crosswalk": {"depends_on": {"nodes": []}},
                }
            }
        )
    )
    return path


DATASETS = [("persons", "conformed"), ("person_crosswalk", "conformed")]


def test_publish_mints_versions_and_catalog(built_db, tmp_path):
    out = tmp_path / "datasets"
    summary = publish(built_db, out, _manifest(tmp_path), datasets=DATASETS)
    assert summary["minted"] == 2

    catalog = json.loads((out / "catalog.json").read_text())
    entry = next(d for d in catalog["datasets"] if d["name"] == "persons")
    version = entry["latest_version"]
    assert entry["tier"] == "conformed"
    assert entry["rows"] == 1
    assert entry["derived_from"] == ["person_crosswalk"]

    package = json.loads((out / "persons" / version / "datapackage.json").read_text())
    [resource] = package["resources"]
    assert resource["hash"].startswith("sha256:")
    field_names = [f["name"] for f in resource["schema"]["fields"]]
    assert field_names == ["entity_id", "name_full"]
    data = (out / "persons" / version / "data.csv").read_text()
    assert "Dana" in data


def test_publish_skips_unchanged(built_db, tmp_path):
    out = tmp_path / "datasets"
    publish(built_db, out, _manifest(tmp_path), datasets=DATASETS)
    summary = publish(built_db, out, _manifest(tmp_path), datasets=DATASETS)
    assert summary["minted"] == 0
    assert summary["unchanged"] == 2
    versions = [p.name for p in (out / "persons").iterdir() if p.is_dir()]
    assert len(versions) == 1


def test_publish_gate_refuses_shrink(built_db, tmp_path):
    out = tmp_path / "datasets"
    con = duckdb.connect(str(built_db))
    con.execute("insert into persons select '01B', 'Two'")
    con.execute("insert into persons select '01C', 'Three'")
    con.execute("insert into persons select '01D', 'Four'")
    con.close()
    publish(built_db, out, _manifest(tmp_path), datasets=DATASETS)

    con = duckdb.connect(str(built_db))
    con.execute("delete from persons where entity_id != '01A'")  # 4 → 1: a 75% shrink
    con.close()
    with pytest.raises(PublishRefused, match="persons"):
        publish(built_db, out, _manifest(tmp_path), datasets=DATASETS)
    # the refused run minted nothing anywhere and the catalog still lists v1
    catalog = json.loads((out / "catalog.json").read_text())
    entry = next(d for d in catalog["datasets"] if d["name"] == "persons")
    assert entry["rows"] == 4


def test_publish_refuses_missing_table(built_db, tmp_path):
    with pytest.raises(PublishRefused, match="nope"):
        publish(
            built_db,
            tmp_path / "datasets",
            _manifest(tmp_path),
            datasets=[("nope", "conformed")],
        )


def test_shrink_gate_overridable_per_run(built_db, tmp_path):
    out = tmp_path / "datasets"
    con = duckdb.connect(str(built_db))
    con.execute("insert into persons select '01B', 'Two'")
    con.close()
    publish(built_db, out, _manifest(tmp_path), datasets=DATASETS)
    con = duckdb.connect(str(built_db))
    con.execute("delete from persons where entity_id != '01A'")
    con.close()
    summary = publish(built_db, out, _manifest(tmp_path), datasets=DATASETS, max_shrink=1.0)
    assert summary["minted"] == 1


def test_refusal_on_a_later_dataset_leaves_no_tmp_orphans(built_db, tmp_path):
    """CR 15: a shrink refusal on dataset k must not strand the tmp dirs
    already staged for 1..k-1 inside the served tree — refusals repeat nightly
    until an operator acts."""
    out = tmp_path / "datasets"
    publish(built_db, out, _manifest(tmp_path), datasets=DATASETS)

    con = duckdb.connect(str(built_db))
    con.execute("delete from person_crosswalk")  # 100% shrink on the SECOND dataset
    con.close()
    with pytest.raises(PublishRefused):
        publish(built_db, out, _manifest(tmp_path), datasets=DATASETS)
    assert list(out.glob(".tmp-*")) == []


def test_startup_sweeps_prior_orphans(built_db, tmp_path):
    """CR 15/42: dirs AND plain files, dataset tmps AND the catalog tmp — every
    orphan shape a crash can leave inside the served tree."""
    out = tmp_path / "datasets"
    out.mkdir()
    stray = out / ".tmp-persons-deadbeef"
    stray.mkdir()
    (stray / "data.csv").write_text("x\n")
    (out / ".tmp-persons-cafe").write_text("a plain-file stray\n")
    (out / ".catalog-beef.tmp").write_text("{}\n")
    publish(built_db, out, _manifest(tmp_path), datasets=DATASETS)
    assert list(out.glob(".tmp-*")) == []
    assert list(out.glob(".catalog-*.tmp")) == []


def test_rebuilt_identical_table_is_skipped_not_reminted(built_db, tmp_path):
    """CR 16: the unchanged hash must survive a table REBUILD — duckdb gives no
    row-order guarantee, so the export orders deterministically."""
    out = tmp_path / "datasets"
    con = duckdb.connect(str(built_db))
    con.execute("drop table persons")
    con.execute(
        "create table persons as "
        "select * from (values ('01A', 'Dana'), ('01B', 'Riley')) t(entity_id, name_full)"
    )
    con.close()
    publish(built_db, out, _manifest(tmp_path), datasets=DATASETS)

    con = duckdb.connect(str(built_db))
    con.execute("drop table persons")
    con.execute(
        "create table persons as "
        "select * from (values ('01B', 'Riley'), ('01A', 'Dana')) t(entity_id, name_full)"
    )
    con.close()
    summary = publish(built_db, out, _manifest(tmp_path), datasets=DATASETS)
    assert summary["unchanged"] == 2
    assert summary["minted"] == 0


def test_the_cutover_tier_is_empty() -> None:
    """#314: `pm_anchors` had a limited life (#354) and it is over.

    power-map#525 re-keyed its assignment crosswalk off the Postgres ULIDs this
    dataset carried and onto the published `assignments.span_key` (usa-wa#370),
    and PM confirmed on #314 that it plans no further seed. So the producer
    stops asserting the mapping rather than shipping a frozen copy of it
    nightly.

    Absence is the entire signal, and it needs no publisher change:
    `catalog.json` is built from `PUBLISHED_DATASETS` alone, so an unlisted
    dataset's entry simply stops appearing on the next run. Version dirs
    already on disk are immutable and stay readable at their URLs — this
    retracts the *forward* assertion, not the archive.

    Asserted as a whole-tier rule rather than one name: `cutover` was minted
    for this dataset alone, so a second entry arriving in it would mean someone
    reopened the seam #314 closed.
    """
    assert [name for name, tier in PUBLISHED_DATASETS if tier == "cutover"] == []


def test_publishes_a_non_dbt_table_with_empty_lineage(built_db, tmp_path) -> None:
    """#354: `pm_anchors` is materialized from Postgres, not by a dbt model, so
    the manifest knows nothing about it. That is honest lineage, not a failure —
    and it must not cost the publisher any special-casing."""
    con = duckdb.connect(str(built_db))
    con.execute(
        "create table pm_anchors as select * from (values "
        "('person', '01A', '01P'), ('assignment', '01B', '01Q')) t(kind, usa_wa_id, pm_id)"
    )
    con.close()
    out = tmp_path / "datasets"

    summary = publish(built_db, out, _manifest(tmp_path), datasets=[("pm_anchors", "cutover")])

    assert summary["minted"] == 1
    entry = next(
        d
        for d in json.loads((out / "catalog.json").read_text())["datasets"]
        if d["name"] == "pm_anchors"
    )
    assert entry["tier"] == "cutover"
    assert entry["rows"] == 2
    assert entry["derived_from"] == []
    # the counts-and-hash manifest.json carried, now carried the way every other
    # catalog entry carries them
    assert entry["hash"].startswith("sha256:")
    assert entry["bytes"] > 0

    package = json.loads(
        (out / "pm_anchors" / entry["latest_version"] / "datapackage.json").read_text()
    )
    [resource] = package["resources"]
    assert [f["name"] for f in resource["schema"]["fields"]] == ["kind", "usa_wa_id", "pm_id"]
    assert resource["hash"] == entry["hash"]


def test_unchanged_dataset_keeps_its_prior_schema_version(built_db, tmp_path, monkeypatch) -> None:
    """A SCHEMA_VERSION bump does not restamp the catalog: an unchanged dataset
    carries its prior entry forward, so its entry keeps naming the contract its
    bytes were actually published under. The live catalog carries a spread of
    versions for exactly this reason — it is the behaviour, not drift."""
    out = tmp_path / "datasets"
    monkeypatch.setattr(publish_mod, "SCHEMA_VERSION", "9.0.0")
    publish(built_db, out, _manifest(tmp_path), datasets=DATASETS)

    monkeypatch.setattr(publish_mod, "SCHEMA_VERSION", "9.1.0")
    con = duckdb.connect(str(built_db))
    con.execute("insert into persons select '01Z', 'Newcomer'")  # only `persons` changes
    con.close()
    summary = publish(built_db, out, _manifest(tmp_path), datasets=DATASETS)

    assert (summary["minted"], summary["unchanged"]) == (1, 1)
    entries = {d["name"]: d for d in json.loads((out / "catalog.json").read_text())["datasets"]}
    assert entries["persons"]["schema_version"] == "9.1.0"
    assert entries["person_crosswalk"]["schema_version"] == "9.0.0"


def test_datapackage_declares_the_csv_dialect(built_db, tmp_path) -> None:
    """#357: the serialisation was an emergent property of publish.py that a
    consumer had to infer by sniffing bytes. A strict parser was guessing, and
    a second producer had nothing to conform to. It ships in the datapackage."""
    out = tmp_path / "datasets"
    publish(built_db, out, _manifest(tmp_path), datasets=DATASETS)

    entry = next(
        d
        for d in json.loads((out / "catalog.json").read_text())["datasets"]
        if d["name"] == "persons"
    )
    package = json.loads(
        (out / "persons" / entry["latest_version"] / "datapackage.json").read_text()
    )
    [resource] = package["resources"]

    assert resource["dialect"] == CSV_DIALECT
    assert resource["dialect"]["lineTerminator"] == "\n"
    assert resource["dialect"]["delimiter"] == ","
    assert resource["dialect"]["header"] is True


def test_published_bytes_obey_the_declared_dialect(built_db, tmp_path) -> None:
    """The declaration is worth nothing unless it is checked against the bytes.
    Parses every published data.csv back with the dialect the datapackage
    declares and confirms it round-trips to the promised shape."""
    out = tmp_path / "datasets"
    con = duckdb.connect(str(built_db))
    con.execute("insert into persons select '01B', 'Quote \"Me\", Please'")  # forces quoting
    con.close()
    publish(built_db, out, _manifest(tmp_path), datasets=DATASETS)

    for entry in json.loads((out / "catalog.json").read_text())["datasets"]:
        version_dir = out / entry["name"] / entry["latest_version"]
        raw = (version_dir / "data.csv").read_bytes()
        dialect = json.loads((version_dir / "datapackage.json").read_text())["resources"][0][
            "dialect"
        ]
        assert b"\r\n" not in raw, f"{entry['name']}: CRLF contradicts the declared lineTerminator"

        rows = list(
            csv.reader(
                io.StringIO(raw.decode()),
                delimiter=dialect["delimiter"],
                quotechar=dialect["quoteChar"],
                doublequote=dialect["doubleQuote"],
            )
        )
        fields = [
            f["name"]
            for f in json.loads((version_dir / "datapackage.json").read_text())["resources"][0][
                "schema"
            ]["fields"]
        ]
        assert rows[0] == fields, f"{entry['name']}: header row must match the declared schema"
        assert len(rows) - 1 == entry["rows"]
        assert rows[1:] == sorted(rows[1:]), (
            f"{entry['name']}: rows must be in `order by all` order"
        )
