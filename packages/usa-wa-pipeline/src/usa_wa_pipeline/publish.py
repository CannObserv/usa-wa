"""The dataset publisher (#311): built pipeline duckdb → the published contract.

    python -m usa_wa_pipeline.publish [--db PATH] [--out DIR] [--max-shrink R]

Materializes each published dataset as an immutable versioned directory —
``<out>/<name>/<version>/data.csv + datapackage.json`` — and flips a thin
``catalog.json`` last (spec § Publication contract):

- **Atomic**: a version dir is staged under a dot-tmp name and renamed into
  place; the catalog is written via tmp+rename only after every dataset
  landed. A crash leaves unlisted orphans, never a listed partial.
- **Skip-if-unchanged**: a dataset whose content hash equals the latest
  version's mints nothing — no version churn on a quiet day.
- **Publish gates** (producer-side; PM's applier gates again): a missing
  table refuses the whole run, and a row-count shrink beyond ``max_shrink``
  (default 10%) refuses it too — retraction=absence makes a degraded harvest
  look like mass retraction, so a shrunken dataset never ships silently.
  ``--max-shrink 1.0`` is the deliberate operator override for a real
  contraction. Nothing mints on a refused run.
- **Lineage** from the dbt manifest (``derived_from`` = the dataset's direct
  model parents), never hand-maintained; the dataset *list* is deliberate
  config (:data:`PUBLISHED_DATASETS` — publishing is a decision).
- Versions are timestamps plus a collision token
  (``v20260903T120000Z-a1b2c3``); the catalog lists only the latest.
  Retention/pruning is deliberately absent: these are archival products at
  ~10^4 rows — sound only because skip-if-unchanged hashes a DETERMINISTIC
  export (``order by all``), so a quiet day mints nothing.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import secrets
import shutil
from datetime import UTC, datetime
from pathlib import Path

import duckdb

from clearinghouse_core.job import JobContext, JobResult, run_job
from clearinghouse_core.logging import get_logger

logger = get_logger(__name__)

#: Stable ledger identity (#178).
JOB_SLUG = "dataset-publish"

#: What gets published: (dataset name == model name, tier). Deliberate config.
#: Staging datasets are the triage/lineage surface (spec § Catalog); conformed
#: products are what PM subscribes to. Assignments/roles join when #309 lands.
PUBLISHED_DATASETS: list[tuple[str, str]] = [
    ("stg_wsl_committees", "staging"),
    ("stg_wsl_sponsors", "staging"),
    ("stg_wsl_committee_members", "staging"),
    ("stg_wsl_meetings", "staging"),
    ("stg_roster_members", "staging"),
    ("stg_pdc_winners", "staging"),
    ("stg_sos_results", "staging"),
    ("stg_sos_filings", "staging"),
    ("stg_raw_fetches", "staging"),
    ("person_crosswalk", "conformed"),
    ("org_crosswalk", "conformed"),
    ("persons", "conformed"),
    ("organizations", "conformed"),
    ("assignments", "conformed"),
    ("roles", "conformed"),
    ("citations", "internal"),
]

#: Tiers that are **not** part of the subscriber contract (#313). An internal
#: dataset is published — same immutable version dirs, same digest, same
#: ``/datasets`` tree, because the deployment loads it exactly the way it loads
#: every other one — but nothing outside this repo is invited to depend on its
#: shape, and it carries no schema-stability promise. `citations` is the first:
#: it exists so ``/provenance/{type}/{id}`` keeps answering once the Postgres
#: provenance tables retire, and its columns follow the API, not consumers.
#:
#: Read by :func:`internal_datasets`, so the distinction is derived from this set
#: rather than restated (CR 103): a constant that names a policy nothing consults
#: is one a later edit can contradict without any gate noticing.
INTERNAL_TIERS = frozenset({"internal"})


def internal_datasets(datasets: list[tuple[str, str]] | None = None) -> frozenset[str]:
    """The published datasets that carry no subscriber contract (#313)."""
    return frozenset(
        name
        for name, tier in (PUBLISHED_DATASETS if datasets is None else datasets)
        if tier in INTERNAL_TIERS
    )


#: Per-dataset schema semver: additive = minor, rename/removal = major (spec).
#: One knob covers every dataset today — per-dataset versions are a later
#: refinement, so a bump re-versions all of them.
#:
#: - 1.1.0 (#309): stg_wsl_committee_members gained the member identity fields
#:   the span tier needs, and `assignments` joined the published set.
#: - 1.2.0 (#309 inc 4): `roles` joined the published set and `assignments`
#:   gained `role_key`. Both additive, hence minor.
#: - 1.3.0 (#313): `roles` gained `entity_id`, its registry ULID — the stable
#:   handle `/api/v1` addresses a role by once it serves from the published
#:   contract. `role_key` stays beside it, so PM's seat match key is unmoved.
#: - 1.4.0 (#313 inc 3): every staging dataset gained `source` + `resource_id`,
#:   the raw coordinates of the wire the row was read from, and two datasets
#:   joined — `stg_raw_fetches` (the attestation dimension) and `citations`
#:   (internal). Appended columns, hence minor.
SCHEMA_VERSION = "1.4.0"

DEFAULT_MAX_SHRINK = 0.10

_TYPE_MAP = {
    "VARCHAR": "string",
    "BIGINT": "integer",
    "INTEGER": "integer",
    "DOUBLE": "number",
    "FLOAT": "number",
    "BOOLEAN": "boolean",
    "DATE": "date",
    "TIMESTAMP": "datetime",
}


class PublishRefused(RuntimeError):
    """A publish gate fired; nothing was minted."""


def _lineage(manifest_path: Path) -> dict[str, list[str]]:
    manifest = json.loads(Path(manifest_path).read_text())
    out = {}
    for node_id, node in manifest.get("nodes", {}).items():
        name = node_id.rsplit(".", 1)[-1]
        parents = [
            parent.rsplit(".", 1)[-1]
            for parent in node.get("depends_on", {}).get("nodes", [])
            if parent.startswith("model.")
        ]
        out[name] = parents
    return out


def _load_catalog(out_root: Path) -> dict:
    path = out_root / "catalog.json"
    if not path.is_file():
        return {"datasets": []}
    return json.loads(path.read_text())


def publish(
    db_path: Path | str,
    out_root: Path | str,
    manifest_path: Path | str,
    *,
    datasets: list[tuple[str, str]] | None = None,
    max_shrink: float = DEFAULT_MAX_SHRINK,
) -> dict[str, int]:
    """Publish every configured dataset. Returns counters; raises on a gate."""
    out_root = Path(out_root)
    out_root.mkdir(parents=True, exist_ok=True)
    # Sweep orphans from prior failed runs (#302 CR 15/42): a refused publish
    # is a ROUTINE outcome that repeats nightly until an operator acts, and its
    # leftovers would accumulate inside the tree /datasets serves. Both shapes:
    # dataset tmp dirs AND the catalog tmp file (rmtree no-ops on plain files,
    # so files need their own unlink). The nightly oneshot is the only
    # publisher, so anything matching here is dead.
    for stray in [*out_root.glob(".tmp-*"), *out_root.glob(".catalog-*.tmp")]:
        if stray.is_dir():
            shutil.rmtree(stray, ignore_errors=True)
        else:
            stray.unlink(missing_ok=True)
    datasets = PUBLISHED_DATASETS if datasets is None else datasets
    lineage = _lineage(Path(manifest_path))
    previous = {d["name"]: d for d in _load_catalog(out_root)["datasets"]}
    # token suffix: two publishes in one second must not collide on the dir name
    version = datetime.now(UTC).strftime("v%Y%m%dT%H%M%SZ") + "-" + secrets.token_hex(3)
    con = duckdb.connect(str(db_path), read_only=True)
    staged: list[dict] = []
    try:
        for name, tier in datasets:
            try:
                columns = con.execute(f'describe "{name}"').fetchall()
            except duckdb.CatalogException as exc:
                raise PublishRefused(f"dataset {name!r}: table missing from the build") from exc
            rows = con.execute(f'select count(*) from "{name}"').fetchone()[0]  # noqa: S608
            prior = previous.get(name)
            # Baseline is the PREVIOUS publish, not a high-water mark: decay
            # under max_shrink per night compounds unseen (~50%/week at 10%).
            # Accepted for now — the parity probes watch absolute counts; a
            # windowed max baseline is the upgrade path if that ever moves.
            if prior and prior["rows"] > 0:
                shrink = (prior["rows"] - rows) / prior["rows"]
                if shrink > max_shrink:
                    raise PublishRefused(
                        f"dataset {name!r}: rows {prior['rows']} → {rows} "
                        f"(shrink {shrink:.0%} > {max_shrink:.0%}); a degraded build "
                        "must not ship as mass retraction — override with --max-shrink "
                        "only for a verified real contraction"
                    )
            tmp_dir = out_root / f".tmp-{name}-{secrets.token_hex(4)}"
            tmp_dir.mkdir(parents=True)
            csv_path = tmp_dir / "data.csv"
            # order by all: duckdb guarantees no row order across rebuilds, and
            # the skip-if-unchanged hash must not churn on identical data (#302 CR)
            con.execute(
                f'copy (select * from "{name}" order by all) '  # noqa: S608
                f"to '{csv_path}' (header, delimiter ',')"
            )
            digest = hashlib.sha256(csv_path.read_bytes()).hexdigest()
            staged.append(
                {
                    "name": name,
                    "tier": tier,
                    "tmp_dir": tmp_dir,
                    "rows": rows,
                    "hash": digest,
                    "bytes": csv_path.stat().st_size,
                    "fields": [
                        {"name": col[0], "type": _TYPE_MAP.get(col[1].split("(")[0], "string")}
                        for col in columns
                    ],
                    "prior": prior,
                }
            )
    except BaseException:
        # A refusal (or any failure) must not strand staged tmp dirs inside the
        # served tree (#302 CR): nothing minted means nothing kept.
        for item in staged:
            shutil.rmtree(item["tmp_dir"], ignore_errors=True)
        raise
    finally:
        con.close()

    counters = {"minted": 0, "unchanged": 0}
    generated_at = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
    catalog_entries = []
    for item in staged:
        prior = item["prior"]
        if prior and prior["hash"] == f"sha256:{item['hash']}":
            counters["unchanged"] += 1
            catalog_entries.append(prior)
            for path in item["tmp_dir"].iterdir():
                path.unlink()
            item["tmp_dir"].rmdir()
            continue
        package = {
            "name": item["name"],
            "version": version,
            "tier": item["tier"],
            "schema_version": SCHEMA_VERSION,
            "derived_from": lineage.get(item["name"], []),
            "generated_at": generated_at,
            "resources": [
                {
                    "name": item["name"],
                    "path": "data.csv",
                    "format": "csv",
                    "hash": f"sha256:{item['hash']}",
                    "bytes": item["bytes"],
                    "rows": item["rows"],
                    "schema": {"fields": item["fields"]},
                }
            ],
        }
        (item["tmp_dir"] / "datapackage.json").write_text(json.dumps(package, indent=2) + "\n")
        final_dir = out_root / item["name"] / version
        final_dir.parent.mkdir(parents=True, exist_ok=True)
        item["tmp_dir"].rename(final_dir)
        counters["minted"] += 1
        catalog_entries.append(
            {
                "name": item["name"],
                "tier": item["tier"],
                "latest_version": version,
                "schema_version": SCHEMA_VERSION,
                "derived_from": lineage.get(item["name"], []),
                "rows": item["rows"],
                "bytes": item["bytes"],
                "hash": f"sha256:{item['hash']}",
                "generated_at": generated_at,
            }
        )
    catalog = {"generated_at": generated_at, "datasets": catalog_entries}
    tmp_catalog = out_root / f".catalog-{secrets.token_hex(4)}.tmp"
    tmp_catalog.write_text(json.dumps(catalog, indent=2) + "\n")
    tmp_catalog.replace(out_root / "catalog.json")
    logger.info("dataset_publish_complete", extra={"version": version, **counters})
    return counters


def _add_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--db", default=None, help="Built duckdb (default USA_WA_PIPELINE_DB).")
    parser.add_argument(
        "--out",
        default=None,
        help="Publish root (default USA_WA_DATASETS_ROOT, else data/datasets).",
    )
    parser.add_argument(
        "--manifest",
        default=None,
        help="dbt manifest.json (default <db dir>/target/manifest.json).",
    )
    parser.add_argument(
        "--max-shrink",
        type=float,
        default=DEFAULT_MAX_SHRINK,
        help="Max per-dataset row shrink ratio before refusing (default 0.10).",
    )


async def _publish_job(ctx: JobContext) -> JobResult:
    db_path = Path(ctx.args.db or os.environ.get("USA_WA_PIPELINE_DB", "data/pipeline.duckdb"))
    out_root = Path(ctx.args.out or os.environ.get("USA_WA_DATASETS_ROOT", "data/datasets"))
    manifest = Path(ctx.args.manifest or db_path.parent / "target" / "manifest.json")
    try:
        counters = publish(db_path, out_root, manifest, max_shrink=ctx.args.max_shrink)
    except PublishRefused as exc:
        logger.error("dataset_publish_refused", extra={"reason": str(exc)})
        return JobResult.failed({"refused": str(exc)}, exit_code=1)
    return JobResult.ok(counters)


def main(argv: list[str] | None = None) -> int:
    """Publish the built datasets. Exit ``1`` = a gate refused (nothing minted)."""
    return run_job(
        JOB_SLUG,
        _publish_job,
        argv=argv,
        prog="python -m usa_wa_pipeline.publish",
        description="Publish versioned dataset snapshots + catalog from the built duckdb (#311).",
        extra_args=_add_args,
        dry_run=False,
    )


if __name__ == "__main__":
    raise SystemExit(main())
