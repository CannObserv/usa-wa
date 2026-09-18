"""Every published dataset's declared contract matches the one it would publish (#385).

`publish.py` declares, per dataset, a history of `ContractRelease`es — a version,
the ordered column list it describes, and why it exists. This builds the dbt
project hermetically and checks the current release against the shape the build
actually produces.

Its value is the failure mode, not the pass: change a published model's columns
and this goes red, and the only way to green it is appending a new release — so
the bump falls out of the mechanism rather than depending on a reviewer noticing
one was owed.

**What this tier can and cannot see.** A hermetic build reads empty sources, and
duckdb types an all-NULL column `INTEGER`, so every column of every model comes
out of it typed `integer` — the types here are a fiction and the declaration
deliberately does not carry them. Names and order it observes exactly, verified
against the live build: on 2026-09-18 all sixteen datasets agreed column-for-
column, differing only in the types this tier cannot see. Types are covered by
the publisher's own gate instead, which compares the full fingerprint — types
included — against what was last published, both sides read from a real build.
"""

import os

import duckdb
import pytest

import usa_wa_pipeline
from usa_wa_pipeline.publish import PUBLISHED_DATASETS

_HERMETIC_ENV = (
    "USA_WA_PIPELINE_DB",
    "USA_WA_PIPELINE_HERMETIC",
    "USA_WA_RAW_ROOT",
    "DATABASE_URL",
)


@pytest.fixture(scope="module")
def hermetic_build(tmp_path_factory):
    """A full `dbt build` into a throwaway duckdb, with no database anywhere.

    Module-scoped: the build is the expensive part (~6s) and every assertion
    below reads the same shapes out of it. `monkeypatch` is function-scoped, so
    the env is saved and restored by hand.
    """
    from dbt.cli.main import dbtRunner

    tmp_path = tmp_path_factory.mktemp("contracts")
    previous = {key: os.environ.get(key) for key in _HERMETIC_ENV}
    os.environ["USA_WA_PIPELINE_DB"] = str(tmp_path / "test.duckdb")
    os.environ["USA_WA_PIPELINE_HERMETIC"] = "1"
    os.environ["USA_WA_RAW_ROOT"] = str(tmp_path / "raw")
    os.environ.pop("DATABASE_URL", None)
    try:
        result = dbtRunner().invoke(
            [
                "build",
                "--project-dir",
                str(usa_wa_pipeline.PROJECT_DIR),
                "--profiles-dir",
                str(usa_wa_pipeline.PROJECT_DIR),
                "--target-path",
                str(tmp_path / "target"),
                "--log-path",
                str(tmp_path / "logs"),
            ]
        )
        assert result.success, f"dbt build failed: {result.exception}"
        yield tmp_path / "test.duckdb"
    finally:
        for key, value in previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


@pytest.fixture(scope="module")
def built_columns(hermetic_build):
    """Every published dataset's column list, as the build produces it.

    A dataset the build does not produce maps to ``None`` rather than raising
    (CR 2). Raising here would end every test in this module in fixture setup,
    so the one test whose subject IS the missing dataset could never report its
    name — the failure would read as an opaque `CatalogException` in whichever
    test happened to run first.
    """
    con = duckdb.connect(str(hermetic_build))
    try:
        built = {}
        for dataset in PUBLISHED_DATASETS:
            try:
                rows = con.execute(f'describe "{dataset.name}"').fetchall()
            except duckdb.CatalogException:
                built[dataset.name] = None
            else:
                built[dataset.name] = tuple(col[0] for col in rows)
        return built
    finally:
        con.close()


@pytest.mark.filterwarnings("ignore::DeprecationWarning")
def test_every_declared_contract_matches_the_build(built_columns) -> None:
    """The drift check, and the reason the version means anything.

    A red here says a published dataset's shape moved while its
    `PUBLISHED_DATASETS` entry stood still. The fix is never to edit the standing
    release: append a new `ContractRelease` carrying the next version and the
    columns printed below, so the history stays a log of what was published.
    """
    drifted = {
        dataset.name: (dataset.schema_version, dataset.columns, built_columns[dataset.name])
        for dataset in PUBLISHED_DATASETS
        if built_columns[dataset.name] is not None
        and dataset.columns != built_columns[dataset.name]
    }
    assert not drifted, "declared contract ≠ built contract:\n" + "\n".join(
        f"  {name} (declared at {version})\n    declared: {declared}\n    built:    {actual}"
        for name, (version, declared, actual) in drifted.items()
    )


@pytest.mark.filterwarnings("ignore::DeprecationWarning")
def test_every_published_dataset_is_in_the_build(built_columns) -> None:
    """A dataset the build does not produce refuses the whole nightly publish
    (`table missing from the build`). Catching that here makes it a red test
    rather than a wedged timer and an operator email."""
    missing = [name for name, columns in built_columns.items() if columns is None]
    assert not missing, f"published but not built: {missing}"


def test_a_dataset_declares_at_least_one_release() -> None:
    """A dataset with no release has no version to publish. Cheap, and it fails
    without paying for the build fixture."""
    for dataset in PUBLISHED_DATASETS:
        assert dataset.releases, f"{dataset.name}: no ContractRelease declared"
        assert dataset.columns, f"{dataset.name}: declares no columns"


def test_release_histories_are_append_only_in_version_order() -> None:
    """A history is a log, so it reads forward. Versions strictly increase, and
    no version is declared twice — a repeated number would mean two different
    contracts published under one, which is the defect #385 filed."""
    for dataset in PUBLISHED_DATASETS:
        versions = [release.version for release in dataset.releases]
        parsed = [tuple(int(part) for part in v.split(".")) for v in versions]
        assert parsed == sorted(parsed), f"{dataset.name}: releases out of order: {versions}"
        assert len(set(versions)) == len(versions), f"{dataset.name}: duplicate version: {versions}"


def test_every_release_explains_itself() -> None:
    """The note is why the number moved, kept beside the number. A changelog
    living anywhere else drifts from the entries it describes — which is how the
    catalog-wide log #385 replaced managed to be accurate and useless at once."""
    for dataset in PUBLISHED_DATASETS:
        for release in dataset.releases:
            assert release.note.strip(), f"{dataset.name} {release.version}: no note"


def test_the_transition_froze_each_dataset_at_the_version_it_was_publishing() -> None:
    """#385's transition: freeze in place, do not renumber.

    These are the values the live catalog carried on 2026-09-18, the day before
    per-dataset versions landed. Resetting them to a clean 1.0.0 would DOWNGRADE
    four datasets on the wire and refuse for the consumer who had just re-pinned
    to major 2; renumbering everything up to 2.0.0 would assert a major change
    for twelve datasets that had none, which is the sin #385 filed. So the
    starting values are arbitrary and harmless — a major is only ever compared
    within a dataset.

    Pinned as data because it is a historical fact about the archive: these
    numbers are the join between what was published before the cutover and what
    is published after, and nothing else records it. It is a statement about
    THESE sixteen, so a dataset published after the cutover is skipped rather
    than failing a lookup (CR 3) — it has no pre-#385 number to have been frozen
    at, and adding one is an ordinary act this test has no opinion about.
    """
    frozen = {
        "stg_wsl_committees": "1.4.0",
        "stg_wsl_sponsors": "1.4.0",
        "stg_wsl_committee_members": "1.4.0",
        "stg_wsl_meetings": "1.6.0",
        "stg_roster_members": "1.4.0",
        "stg_pdc_winners": "1.4.0",
        "stg_sos_results": "1.4.0",
        "stg_sos_filings": "1.4.0",
        "stg_raw_fetches": "2.0.0",
        "person_crosswalk": "2.0.0",
        "org_crosswalk": "1.2.0",
        "persons": "2.0.0",
        "organizations": "1.0.0",
        "assignments": "1.7.0",
        "roles": "1.3.0",
        "citations": "2.0.0",
    }
    for dataset in PUBLISHED_DATASETS:
        if dataset.name not in frozen:
            continue  # published after the cutover: no pre-#385 number to freeze
        baseline = dataset.releases[0]
        assert baseline.version == frozen[dataset.name], (
            f"{dataset.name}: the frozen baseline is {frozen[dataset.name]}, "
            f"not {baseline.version} — the cutover does not renumber"
        )
