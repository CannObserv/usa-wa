"""The raw attestation dimension (#313): every source's newest ok fetch.

`stg_raw_fetches` is the file-tier analog of the Postgres ``FetchEvent`` the
citations chain used to hang off. One row per (source, resource_id) — what
`latest.json` indexes — enriched from the run manifest that recorded it, which
is where the URL and byte count live.
"""

import json

from clearinghouse_core.rawstore import RawStore
from usa_wa_pipeline.staging import fetches


def _store(root, source: str) -> RawStore:
    return RawStore(root, source)


def test_one_row_per_resource_across_every_source(tmp_path) -> None:
    for source, resources in (
        ("usa_wa_legislature", {"sponsors:2025-26": b"a", "committees-roster:2025-26": b"b"}),
        ("usa_wa_pdc", {"house-winners:2024": b"c"}),
    ):
        run = _store(tmp_path, source).open_run()
        for resource_id, body in resources.items():
            run.record(resource_id, body, url=f"https://example/{resource_id}")
        run.close()

    rows = fetches.fetch_rows(tmp_path)
    assert [(r["source"], r["resource_id"]) for r in rows] == [
        ("usa_wa_legislature", "committees-roster:2025-26"),
        ("usa_wa_legislature", "sponsors:2025-26"),
        ("usa_wa_pdc", "house-winners:2024"),
    ]


def test_the_manifest_supplies_url_and_size(tmp_path) -> None:
    run = _store(tmp_path, "usa_wa_pdc").open_run()
    run.record("house-winners:2024", b"payload", url="https://pdc/house/2024")
    run.close()

    [row] = fetches.fetch_rows(tmp_path)
    assert row["url"] == "https://pdc/house/2024"
    assert row["bytes"] == len(b"payload")
    assert row["sha256"] and row["fetched_at"] and row["run_id"]


def test_the_newest_fetch_wins_and_names_its_own_run(tmp_path) -> None:
    store = _store(tmp_path, "usa_wa_pdc")
    first = store.open_run()
    first.record("house-winners:2024", b"old", url="https://old")
    first.close()
    second = store.open_run()
    second.record("house-winners:2024", b"new", url="https://new")
    second.close()

    [row] = fetches.fetch_rows(tmp_path)
    assert row["url"] == "https://new"
    assert (
        row["run_id"]
        == json.loads((tmp_path / "usa_wa_pdc" / "latest.json").read_text())["house-winners:2024"][
            "run_id"
        ]
    )


def test_a_pruned_manifest_still_yields_the_attestation(tmp_path) -> None:
    """The digest is the integrity baseline and lives in `latest.json`; the URL
    is manifest colour. Losing the manifest must not lose the citation — that
    would silently unmoor every entity the resource attests."""
    store = _store(tmp_path, "usa_wa_pdc")
    run = store.open_run()
    run.record("house-winners:2024", b"payload", url="https://pdc/house/2024")
    run.close()
    for path in store.manifest_paths():
        path.unlink()

    [row] = fetches.fetch_rows(tmp_path)
    assert row["sha256"] and row["url"] is None and row["bytes"] is None


def test_a_source_that_never_succeeded_contributes_nothing(tmp_path) -> None:
    (tmp_path / "usa_wa_sos").mkdir(parents=True)
    (tmp_path / "usa_wa_sos" / "latest.json").write_text("{}")
    assert fetches.fetch_rows(tmp_path) == []


def test_an_absent_raw_root_is_empty_not_an_error(tmp_path) -> None:
    assert fetches.fetch_rows(tmp_path / "nowhere") == []


def test_each_run_manifest_is_read_once_not_once_per_resource(tmp_path, monkeypatch) -> None:
    """CR 101: the cache was spelled with `setdefault`, whose default argument is
    evaluated EAGERLY — so the manifest was re-read and re-parsed on every row.
    Measured against the live store that was 1,391 reads for 8 manifests, while
    the comment above it claimed the opposite."""
    run = _store(tmp_path, "usa_wa_pdc").open_run()
    for year in range(2000, 2020, 2):
        run.record(f"house-winners:{year}", f"w{year}".encode(), url=f"https://pdc/{year}")
    run.close()

    reads: list[str] = []
    real = fetches.Path.read_text

    def counting(self, *args, **kwargs):
        if self.parent.name == "runs":
            reads.append(self.name)
        return real(self, *args, **kwargs)

    monkeypatch.setattr(fetches.Path, "read_text", counting)
    rows = fetches.fetch_rows(tmp_path)

    assert len(rows) == 10
    assert len(reads) == 1, f"one run, one manifest read; got {len(reads)}"
