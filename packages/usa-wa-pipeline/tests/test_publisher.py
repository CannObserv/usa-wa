"""The dataset publisher (#311): built duckdb → immutable versions + catalog."""

import csv
import io
import json
from datetime import UTC, datetime, timedelta

import duckdb
import pytest

from usa_wa_pipeline.publish import (
    CSV_DIALECT,
    PUBLISH_GRACE,
    PUBLISHED_DATASETS,
    ContractRelease,
    PublishedDataset,
    PublishRefused,
    contract_fingerprint,
    publish,
    stale_after,
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


def _dataset(name, tier="conformed", *, version="1.0.0", columns=("entity_id",)):
    """A published dataset for tests whose subject is not the contract history."""
    return PublishedDataset(name, tier, (ContractRelease(version, columns, "test baseline"),))


DATASETS = [_dataset("persons"), _dataset("person_crosswalk")]


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
            datasets=[_dataset("nope")],
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
    assert [d.name for d in PUBLISHED_DATASETS if d.tier == "cutover"] == []


def test_publishes_a_non_dbt_table_with_empty_lineage(built_db, tmp_path) -> None:
    """A table the dbt manifest knows nothing about publishes with `derived_from:
    []` — honest lineage, not a failure, and no special-casing in the publisher.

    `pm_anchors` (#354) was the live case until #314 retired it, so this is now
    a property of `publish` with no dataset exercising it. Kept deliberately:
    the behaviour being pinned is the *absence* of a special case, which is
    exactly what decays once nothing checks it."""
    con = duckdb.connect(str(built_db))
    con.execute(
        "create table unmodelled as select * from (values "
        "('person', '01A', '01P'), ('assignment', '01B', '01Q')) t(kind, usa_wa_id, pm_id)"
    )
    con.close()
    out = tmp_path / "datasets"

    summary = publish(
        built_db, out, _manifest(tmp_path), datasets=[_dataset("unmodelled", "internal")]
    )

    assert summary["minted"] == 1
    entry = next(
        d
        for d in json.loads((out / "catalog.json").read_text())["datasets"]
        if d["name"] == "unmodelled"
    )
    assert entry["tier"] == "internal"
    assert entry["rows"] == 2
    assert entry["derived_from"] == []
    # the counts-and-hash manifest.json carried, now carried the way every other
    # catalog entry carries them
    assert entry["hash"].startswith("sha256:")
    assert entry["bytes"] > 0

    package = json.loads(
        (out / "unmodelled" / entry["latest_version"] / "datapackage.json").read_text()
    )
    [resource] = package["resources"]
    assert [f["name"] for f in resource["schema"]["fields"]] == ["kind", "usa_wa_id", "pm_id"]
    assert resource["hash"] == entry["hash"]


def test_an_unchanged_dataset_keeps_its_own_version(built_db, tmp_path) -> None:
    """A quiet dataset mints nothing, and its entry keeps ITS version — not the
    version of whichever sibling happened to mint beside it (#385).

    This is what the catalog-wide constant could not express: before #385 the
    two datasets below would have come out of the second run holding different
    numbers for reasons having nothing to do with either one's shape."""
    out = tmp_path / "datasets"
    datasets = [_dataset("persons", version="1.4.0"), _dataset("person_crosswalk", version="2.0.0")]
    publish(built_db, out, _manifest(tmp_path), datasets=datasets)

    con = duckdb.connect(str(built_db))
    con.execute("insert into persons select '01Z', 'Newcomer'")  # only `persons` changes
    con.close()
    summary = publish(built_db, out, _manifest(tmp_path), datasets=datasets)

    assert (summary["minted"], summary["unchanged"]) == (1, 1)
    entries = {d["name"]: d for d in json.loads((out / "catalog.json").read_text())["datasets"]}
    assert entries["persons"]["schema_version"] == "1.4.0"
    assert entries["person_crosswalk"]["schema_version"] == "2.0.0"


def test_a_version_bump_re_mints_a_dataset_whose_bytes_did_not_move(built_db, tmp_path) -> None:
    """#385: skip-if-unchanged hashed data.csv ALONE, so a metadata-only change
    to the contract never reached a dataset at all.

    #357's `dialect` is the proof — PIPELINE-PUBLICATION.md had to carry that
    declaration by hand for every version dir that had not re-minted since.
    Per-dataset versions make it sharper, because for some changes the bump IS
    the only wire difference: an unpropagated bump is a version nobody can read.
    So the mint decision is data hash OR contract, and the cost is one version
    dir with a byte-identical data.csv per contract change — rare by
    construction, and the thing that makes a bump observable."""
    out = tmp_path / "datasets"
    publish(built_db, out, _manifest(tmp_path), datasets=[_dataset("persons", version="1.4.0")])

    bumped = PublishedDataset(
        "persons",
        "conformed",
        (
            ContractRelease("1.4.0", ("entity_id",), "test baseline"),
            ContractRelease("1.5.0", ("entity_id",), "a restatement the bytes cannot show"),
        ),
    )
    summary = publish(built_db, out, _manifest(tmp_path), datasets=[bumped])

    assert summary["minted"] == 1
    versions = sorted(p.name for p in (out / "persons").iterdir() if p.is_dir())
    assert len(versions) == 2
    entry = json.loads((out / "catalog.json").read_text())["datasets"][0]
    assert entry["schema_version"] == "1.5.0"
    assert (out / "persons" / versions[0] / "data.csv").read_bytes() == (
        out / "persons" / versions[1] / "data.csv"
    ).read_bytes(), "the bytes did not move; only the contract they ship under did"


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


# --- #385: per-dataset schema versions, gated by a contract fingerprint -------


def test_contract_fingerprint_moves_with_shape_and_not_with_lineage() -> None:
    """The fingerprint answers the only question a consumer asks of a version
    field: did the shape I bound to change?

    Order is load-bearing — "an appended column is a minor" only holds for a
    positional reader, so a reordering is a different contract.

    Lineage's exclusion is pinned by
    `test_the_contract_hash_is_blind_to_a_lineage_change` against a real
    publish, not here: it used to be asserted by passing `derived_from` to a
    parameter this function ignored, which is the same defect #385 filed — a
    field that does not mean what its name says (CR 6)."""
    base = contract_fingerprint(
        name="persons",
        tier="conformed",
        fields=[{"name": "entity_id", "type": "string"}, {"name": "name_full", "type": "string"}],
    )

    reordered = contract_fingerprint(
        name="persons",
        tier="conformed",
        fields=[{"name": "name_full", "type": "string"}, {"name": "entity_id", "type": "string"}],
    )
    retyped = contract_fingerprint(
        name="persons",
        tier="conformed",
        fields=[{"name": "entity_id", "type": "string"}, {"name": "name_full", "type": "integer"}],
    )
    appended = contract_fingerprint(
        name="persons",
        tier="conformed",
        fields=[
            {"name": "entity_id", "type": "string"},
            {"name": "name_full", "type": "string"},
            {"name": "party", "type": "string"},
        ],
    )
    demoted = contract_fingerprint(
        name="persons",
        tier="internal",
        fields=[{"name": "entity_id", "type": "string"}, {"name": "name_full", "type": "string"}],
    )
    redialected = contract_fingerprint(
        name="persons",
        tier="conformed",
        fields=[{"name": "entity_id", "type": "string"}, {"name": "name_full", "type": "string"}],
        dialect={**CSV_DIALECT, "delimiter": "\t"},
    )
    assert len({base, reordered, retyped, appended, demoted, redialected}) == 6


def test_each_dataset_carries_its_own_version() -> None:
    """#385: the version is per-dataset, not one module constant stamped onto
    whatever minted next. A dataset's current version is the last release it
    declares, and that is what reaches its datapackage."""
    dataset = PublishedDataset(
        "persons",
        "conformed",
        (
            ContractRelease("1.0.0", ("entity_id", "name_full"), "the first published contract"),
            ContractRelease("1.1.0", ("entity_id", "name_full", "party"), "gained `party`"),
        ),
    )
    assert dataset.schema_version == "1.1.0"
    assert dataset.columns == ("entity_id", "name_full", "party")


def test_each_dataset_publishes_its_own_version_and_contract_hash(built_db, tmp_path) -> None:
    """#385: the version written into a dataset's datapackage is ITS version.

    Two datasets minting in the same run, declaring different versions, must not
    come out carrying the same number — that is the whole defect. The computed
    `contract_hash` ships beside it, because the question a consumer actually
    asks is "is this the shape I validated?", and a hash answers it where a
    major can lie."""
    out = tmp_path / "datasets"
    datasets = [
        _dataset("persons", version="1.4.0"),
        _dataset("person_crosswalk", version="2.0.0"),
    ]
    publish(built_db, out, _manifest(tmp_path), datasets=datasets)

    entries = {d["name"]: d for d in json.loads((out / "catalog.json").read_text())["datasets"]}
    assert entries["persons"]["schema_version"] == "1.4.0"
    assert entries["person_crosswalk"]["schema_version"] == "2.0.0"

    package = json.loads(
        (out / "persons" / entries["persons"]["latest_version"] / "datapackage.json").read_text()
    )
    assert package["schema_version"] == "1.4.0"
    assert package["contract_hash"] == entries["persons"]["contract_hash"]
    assert package["contract_hash"] == contract_fingerprint(
        name="persons",
        tier="conformed",
        fields=[{"name": "entity_id", "type": "string"}, {"name": "name_full", "type": "string"}],
    )


def test_a_contract_change_without_a_bump_refuses_the_run(built_db, tmp_path) -> None:
    """The gate #385 asks for: a dataset's `schema_version` changes if its
    published contract changes.

    Enforced ONE WAY — change implies bump, hard refusal — and not as the "if and
    only if" the issue's acceptance bullet asks for, because the reverse is
    unimplementable: the published fields are `{name, type}` with no descriptions
    and no contract prose, so a semantics-only change (a column re-derived, its
    meaning shifted, its type unmoved) has no fingerprint expression. Enforcing
    the iff would forbid the honest major.

    Refusal, not a warning, and refusal of the WHOLE run, because that is what
    every other publish gate does — a partial catalog is the one outcome the
    publisher never produces."""
    out = tmp_path / "datasets"
    datasets = [_dataset("persons", version="1.4.0"), _dataset("person_crosswalk")]
    publish(built_db, out, _manifest(tmp_path), datasets=datasets)

    con = duckdb.connect(str(built_db))
    con.execute("alter table persons add column party varchar")  # an appended column: a minor
    con.close()

    with pytest.raises(PublishRefused, match="persons.*1.4.0"):
        publish(built_db, out, _manifest(tmp_path), datasets=datasets)
    # refused whole: the sibling that WOULD have minted cleanly minted nothing
    assert list(out.glob(".tmp-*")) == []
    catalog = json.loads((out / "catalog.json").read_text())
    persons = next(d for d in catalog["datasets"] if d["name"] == "persons")
    assert [f for f in persons] and persons["schema_version"] == "1.4.0"
    assert len([p for p in (out / "persons").iterdir() if p.is_dir()]) == 1


def test_a_contract_change_publishes_once_its_version_moves(built_db, tmp_path) -> None:
    """The other half of the gate: with the bump declared, the same change ships.

    The fingerprint the publisher writes is COMPUTED from the build, never
    assembled from the declared columns — the datapackage describes the bytes
    beside it, and a stale declaration must not be able to make it lie. Keeping
    the declaration honest is the drift test's job
    (`test_published_contracts.py`), where a wrong one is a build failure rather
    than a published falsehood."""
    out = tmp_path / "datasets"
    publish(built_db, out, _manifest(tmp_path), datasets=[_dataset("persons", version="1.4.0")])

    con = duckdb.connect(str(built_db))
    con.execute("alter table persons add column party varchar")
    con.close()
    bumped = PublishedDataset(
        "persons",
        "conformed",
        (
            ContractRelease("1.4.0", ("entity_id",), "test baseline"),
            ContractRelease("1.5.0", ("entity_id", "party"), "gained `party`, appended"),
        ),
    )
    summary = publish(built_db, out, _manifest(tmp_path), datasets=[bumped])

    assert summary["minted"] == 1
    entry = json.loads((out / "catalog.json").read_text())["datasets"][0]
    assert entry["schema_version"] == "1.5.0"
    assert entry["contract_hash"] == contract_fingerprint(
        name="persons",
        tier="conformed",
        fields=[
            {"name": "entity_id", "type": "string"},
            {"name": "name_full", "type": "string"},
            {"name": "party", "type": "string"},
        ],
    )


def test_the_gate_is_silent_on_a_datasets_first_publish(built_db, tmp_path) -> None:
    """A dataset with no prior published entry has no contract to have changed.
    It mints at whatever version it declares — a new dataset joining is not a
    contract change to itself, and the gate must not make one unpublishable."""
    out = tmp_path / "datasets"
    summary = publish(
        built_db,
        out,
        _manifest(tmp_path),
        datasets=[_dataset("persons", version="3.1.0")],
    )
    assert summary["minted"] == 1
    assert (
        json.loads((out / "catalog.json").read_text())["datasets"][0]["schema_version"] == "3.1.0"
    )


def test_the_gate_holds_across_the_pre_385_catalog(built_db, tmp_path) -> None:
    """Entries minted before #385 carry no `contract_hash`, and the gate must not
    read their absence as a contract change — that would refuse the first run
    after deploy for all sixteen datasets, on a repo where nothing had moved.

    The baseline is adopted instead: an entry with no recorded contract re-mints
    (its datapackage is missing a field the current one publishes) and the run
    proceeds."""
    out = tmp_path / "datasets"
    datasets = [_dataset("persons", version="1.4.0")]
    publish(built_db, out, _manifest(tmp_path), datasets=datasets)

    catalog_path = out / "catalog.json"
    catalog = json.loads(catalog_path.read_text())
    for entry in catalog["datasets"]:
        del entry["contract_hash"]  # a pre-#385 catalog
    catalog_path.write_text(json.dumps(catalog, indent=2) + "\n")

    summary = publish(built_db, out, _manifest(tmp_path), datasets=datasets)
    assert summary["minted"] == 1
    entry = json.loads(catalog_path.read_text())["datasets"][0]
    assert entry["schema_version"] == "1.4.0"
    assert entry["contract_hash"].startswith("sha256:")


def test_a_version_below_the_published_one_refuses_the_run(built_db, tmp_path) -> None:
    """CR 1: a declared version that sorts BELOW what was last published is a
    downgrade, and refuses.

    The gate above catches a contract that moved without its version. This
    catches the other direction — a version that moved backwards, by an edited
    history or a mistyped release — and it has to live here because it is the
    only place that can see it: CI has no published state to compare against.
    A downgrade is the precise wire break #385 exists to prevent, since a
    consumer pinned to the major it last saw starts refusing the dataset."""
    out = tmp_path / "datasets"
    publish(built_db, out, _manifest(tmp_path), datasets=[_dataset("persons", version="2.0.0")])

    with pytest.raises(PublishRefused, match="1.0.0.*2.0.0"):
        publish(built_db, out, _manifest(tmp_path), datasets=[_dataset("persons", version="1.0.0")])
    assert list(out.glob(".tmp-*")) == []
    entry = json.loads((out / "catalog.json").read_text())["datasets"][0]
    assert entry["schema_version"] == "2.0.0"


def test_a_version_equal_to_the_published_one_is_the_quiet_case(built_db, tmp_path) -> None:
    """The overwhelmingly common run: nothing moved, so the version did not
    either. Equality is not a downgrade, and the monotonicity check must not
    turn every quiet night into a refusal."""
    out = tmp_path / "datasets"
    datasets = [_dataset("persons", version="2.0.0")]
    publish(built_db, out, _manifest(tmp_path), datasets=datasets)
    summary = publish(built_db, out, _manifest(tmp_path), datasets=datasets)
    assert (summary["minted"], summary["unchanged"]) == (0, 1)


def test_a_double_digit_version_compares_numerically_not_lexically(built_db, tmp_path) -> None:
    """`"1.10.0" < "1.9.0"` as strings and `>` as versions. Comparing the wrong
    way would refuse the eleventh minor of a dataset as a downgrade — a failure
    that waits years and then fires on a correct change."""
    out = tmp_path / "datasets"
    publish(built_db, out, _manifest(tmp_path), datasets=[_dataset("persons", version="1.9.0")])
    summary = publish(
        built_db, out, _manifest(tmp_path), datasets=[_dataset("persons", version="1.10.0")]
    )
    assert summary["minted"] == 1
    assert (
        json.loads((out / "catalog.json").read_text())["datasets"][0]["schema_version"] == "1.10.0"
    )


def test_the_contract_hash_is_blind_to_a_lineage_change(built_db, tmp_path) -> None:
    """CR 6: `derived_from` moves and `contract_hash` does not.

    Lineage is provenance, not shape. It comes from the dbt manifest, so folding
    it into the fingerprint would churn every downstream dataset's version
    whenever an *intermediate* model was refactored — a version bump a consumer
    would have to evaluate, describing a change they cannot see.

    Asserted end-to-end rather than by handing the hash function a lineage it
    ignores: the property that matters is what the publisher writes."""
    out = tmp_path / "datasets"
    datasets = [_dataset("persons", version="1.0.0")]
    publish(built_db, out, _manifest(tmp_path), datasets=datasets)
    first = json.loads((out / "catalog.json").read_text())["datasets"][0]

    # a different manifest AND a row change. The row change is load-bearing: a
    # carried-forward entry copies `contract_hash` from the prior one, so without
    # a re-mint the hash assertion below holds trivially. `derived_from` alone
    # would move either way (#388).
    con = duckdb.connect(str(built_db))
    con.execute("insert into persons select '01Z', 'Newcomer'")
    con.close()
    publish(built_db, out, _relineaged_manifest(tmp_path), datasets=datasets)
    second = json.loads((out / "catalog.json").read_text())["datasets"][0]

    assert first["derived_from"] == ["person_crosswalk"]
    assert second["derived_from"] == ["int_person_identities"]
    assert second["contract_hash"] == first["contract_hash"]
    assert second["schema_version"] == first["schema_version"] == "1.0.0"


def _relineaged_manifest(tmp_path):
    """The manifest after a refactor: `persons` now derives from a renamed intermediate."""
    path = tmp_path / "relineaged.json"
    path.write_text(
        json.dumps(
            {
                "nodes": {
                    "model.usa_wa_pipeline.persons": {
                        "depends_on": {"nodes": ["model.usa_wa_pipeline.int_person_identities"]}
                    }
                }
            }
        )
    )
    return path


def test_a_quiet_dataset_publishes_its_current_lineage(built_db, tmp_path) -> None:
    """#388: a carried-forward catalog entry gets today's `derived_from`.

    The entry used to be carried forward verbatim, so a dataset whose bytes and
    contract were both quiet kept publishing the parents it had at its last mint,
    as though they were current. Lineage does not mint (CR 6), so the refresh
    moves the catalog alone: the version dir's datapackage is the historical
    record of what that version was built from, and stays as it shipped."""
    out = tmp_path / "datasets"
    datasets = [_dataset("persons")]
    publish(built_db, out, _manifest(tmp_path), datasets=datasets)
    first = json.loads((out / "catalog.json").read_text())["datasets"][0]

    summary = publish(built_db, out, _relineaged_manifest(tmp_path), datasets=datasets)
    second = json.loads((out / "catalog.json").read_text())["datasets"][0]

    assert (summary["minted"], summary["unchanged"]) == (0, 1)
    assert second["derived_from"] == ["int_person_identities"]
    assert second["latest_version"] == first["latest_version"]
    assert second["generated_at"] == first["generated_at"]
    package = out / "persons" / first["latest_version"] / "datapackage.json"
    assert json.loads(package.read_text())["derived_from"] == ["person_crosswalk"]


def test_a_lineage_refresh_is_counted_only_when_the_parents_moved(built_db, tmp_path) -> None:
    """#388: a refactor that re-parents a quiet dataset shows up in the run's log
    line once; a quiet day with the same manifest counts nothing, and neither
    does every night after the refactor — the refreshed parents are written back."""
    out = tmp_path / "datasets"
    datasets = [_dataset("persons"), _dataset("person_crosswalk")]
    publish(built_db, out, _manifest(tmp_path), datasets=datasets)

    quiet = publish(built_db, out, _manifest(tmp_path), datasets=datasets)
    refactored = publish(built_db, out, _relineaged_manifest(tmp_path), datasets=datasets)
    settled = publish(built_db, out, _relineaged_manifest(tmp_path), datasets=datasets)

    assert quiet["lineage_refreshed"] == 0
    # `persons` moved; `person_crosswalk` had no parents before or after
    assert refactored["lineage_refreshed"] == 1
    assert settled["lineage_refreshed"] == 0


def test_the_catalog_carries_the_publishers_heartbeat(built_db, tmp_path) -> None:
    """#386: the run time and the staleness threshold, under names no entry shares.

    The run time used to be a top-level ``generated_at`` ten lines above a
    per-entry ``generated_at`` meaning mint time. A consumer had a coin-flip
    chance of reading the one that never advances on a quiet day."""
    out = tmp_path / "datasets"
    publish(built_db, out, _manifest(tmp_path), datasets=DATASETS)
    catalog = json.loads((out / "catalog.json").read_text())

    assert "generated_at" not in catalog
    checked_at = catalog["checked_at"]
    checked = datetime.strptime(checked_at, "%Y-%m-%dT%H:%M:%S.%fZ").replace(tzinfo=UTC)
    assert catalog["stale_after"] == stale_after(checked).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
    # a minting run stamps its versions with the same clock it stamps the catalog
    assert {entry["generated_at"] for entry in catalog["datasets"]} == {checked_at}


def test_a_run_that_mints_nothing_still_advances_the_heartbeat(built_db, tmp_path) -> None:
    """#386: the whole point — a quiet day and a dead pipeline must differ on the wire."""
    out = tmp_path / "datasets"
    publish(built_db, out, _manifest(tmp_path), datasets=DATASETS)
    first = json.loads((out / "catalog.json").read_text())

    summary = publish(built_db, out, _manifest(tmp_path), datasets=DATASETS)
    second = json.loads((out / "catalog.json").read_text())

    assert summary["minted"] == 0
    assert second["checked_at"] > first["checked_at"]
    # and the per-entry mint time does NOT move: it is the version's, not the run's
    assert [e["generated_at"] for e in second["datasets"]] == [
        e["generated_at"] for e in first["datasets"]
    ]


def test_a_refused_run_leaves_the_heartbeat_stale(built_db, tmp_path) -> None:
    """#386: a refusal publishes nothing, so it must not claim a fresh check."""
    out = tmp_path / "datasets"
    publish(built_db, out, _manifest(tmp_path), datasets=DATASETS)
    before = json.loads((out / "catalog.json").read_text())["checked_at"]

    con = duckdb.connect(str(built_db))
    con.execute("delete from person_crosswalk")
    con.close()
    with pytest.raises(PublishRefused):
        publish(built_db, out, _manifest(tmp_path), datasets=DATASETS)

    assert json.loads((out / "catalog.json").read_text())["checked_at"] == before


def _utc(stamp: str) -> datetime:
    return datetime.fromisoformat(stamp).replace(tzinfo=UTC)


def test_stale_after_is_the_next_scheduled_run_plus_its_grace() -> None:
    """#386: a deadline, from the schedule — the next run after the check, not the check's age."""
    assert stale_after(_utc("2026-09-22T08:05:42")) == _utc("2026-09-23T08:00") + PUBLISH_GRACE
    # an off-schedule run (a boot catch-up, a by-hand re-run) before today's run
    assert stale_after(_utc("2026-09-22T07:10:00")) == _utc("2026-09-22T08:00") + PUBLISH_GRACE
    # a check landing exactly on the schedule is that run, so the next is tomorrow's
    assert stale_after(_utc("2026-09-22T08:00:00")) == _utc("2026-09-23T08:00") + PUBLISH_GRACE


def test_one_missed_run_is_stale_at_the_subscribers_next_daily_pull() -> None:
    """#386 CR 1: the case a duration threshold could not see.

    power-map pulls daily at 09:00 UTC; the chain publishes around 08:05. With a
    26h ``stale_after_seconds``, a missed night read 24h55m old at the next pull —
    fresh — and the following night published before the pull after that, so a
    single miss never showed. The deadline has passed by 09:00 the day of the miss.
    """
    last_good = _utc("2026-09-22T08:05:42")
    deadline = stale_after(last_good)
    assert _utc("2026-09-23T09:00") > deadline
    # and no false alarm inside the next run's own publish window
    assert _utc("2026-09-23T08:30") < deadline
    assert deadline - _utc("2026-09-23T08:00") < timedelta(hours=1)
