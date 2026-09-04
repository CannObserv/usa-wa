"""The dataset publisher (#311): built duckdb → immutable versions + catalog."""

import json

import duckdb
import pytest

from usa_wa_pipeline import publish as publish_module
from usa_wa_pipeline.publish import PublishRefused, publish


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


def test_the_internal_tier_is_derived_from_the_dataset_config() -> None:
    """CR 103: `INTERNAL_TIERS` named a policy nothing consulted, so a dataset
    could gain a subscriber-facing tier with no gate noticing. Deriving the set
    from `PUBLISHED_DATASETS` is what keeps the two from drifting apart."""
    assert publish_module.internal_datasets() == {"citations"}
    assert "persons" not in publish_module.internal_datasets()


def test_a_dataset_whose_tier_is_not_internal_is_a_subscriber_product() -> None:
    assert publish_module.internal_datasets([("x", "conformed"), ("y", "internal")]) == {"y"}
