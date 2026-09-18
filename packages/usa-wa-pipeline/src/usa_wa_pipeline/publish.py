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
  config (:data:`PUBLISHED_DATASETS` — publishing is a decision). A table with
  no dbt model behind it publishes with empty lineage rather than being
  special-cased, which is what let ``pm_anchors`` (#354) ride this path until
  #314 retired it. No live dataset needs the fallback today; it stays because
  special-casing is the thing being avoided.
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
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import duckdb

from clearinghouse_core.job import JobContext, JobResult, run_job
from clearinghouse_core.logging import get_logger

logger = get_logger(__name__)

#: Stable ledger identity (#178).
JOB_SLUG = "dataset-publish"


@dataclass(frozen=True)
class ContractRelease:
    """One version of one dataset's published contract (#385).

    ``columns`` is the ordered field list that version describes, and it is the
    whole mechanism: change a published model's columns and
    ``test_every_declared_contract_matches_the_build`` goes red, and the only way
    to green it is appending a new release. The bump falls out of the mechanism
    rather than depending on a reviewer noticing one was owed.

    It records NAMES, not types, because names are what a database-free build can
    observe. The hermetic dbt build (``USA_WA_PIPELINE_HERMETIC=1``) reads empty
    sources, and duckdb types an all-NULL column ``INTEGER`` — so every column of
    every model comes out of it typed ``integer``, and nothing about the real
    types is knowable there. Declaring them would be declaring a fiction. Types
    are covered instead by the publisher's own gate below, which compares the
    full fingerprint — types included — against what was LAST PUBLISHED, both
    sides read from a real build.

    Names over a digest for the same reason a diff beats a checksum in review: a
    contract change reads here as ``+ "party"``, which a reviewer can check
    against the model. An opaque hash is unreviewable, and a stale one is
    unverifiable in a tier that cannot recompute it.

    ``note`` is why the version exists, kept beside the number rather than in a
    module-level changelog that drifts from the entries it describes.
    """

    version: str
    columns: tuple[str, ...]
    note: str


@dataclass(frozen=True)
class PublishedDataset:
    """A dataset the publisher asserts, and the history of its contract (#385).

    The version is PER-DATASET. It was one module constant stamped onto whatever
    minted next, which made a dataset's major encode *when it last minted* rather
    than what its shape is: carry-forward plus skip-if-unchanged spread seven
    values across sixteen datasets at once, two of them two majors apart with
    identical contracts. power-map's puller pins a major and refused ``persons``
    and ``person_crosswalk`` over #314's 2.0.0 bump, whose entire content was
    ``pm_anchors`` leaving the catalog — neither dataset's shape had moved by a
    single field. A consumer correctly implementing semver was refusing a dataset
    over a bump that asserted nothing about it.
    """

    name: str
    tier: str
    releases: tuple[ContractRelease, ...]

    @property
    def schema_version(self) -> str:
        """The version this dataset publishes today: its latest release."""
        return self.releases[-1].version

    @property
    def columns(self) -> tuple[str, ...]:
        """The ordered field list :attr:`schema_version` claims to describe."""
        return self.releases[-1].columns


def contract_fingerprint(
    *,
    name: str,
    tier: str,
    fields: Sequence[dict[str, str]],
    dialect: dict[str, object] | None = None,
    derived_from: Sequence[str] | None = None,
) -> str:
    """Hash the published contract of one dataset (#385).

    Covers exactly what a consumer binds to: the dataset's name, its tier (the
    subscribe signal — ``conformed`` → ``internal`` is a contract downgrade), the
    ORDERED field list with types, and the CSV dialect. Order is in because "an
    appended column is a minor" only holds for a positional reader.

    ``derived_from`` is accepted and deliberately ignored. Lineage is provenance,
    not shape, and it comes from the dbt manifest — folding it in would churn
    every downstream dataset's version whenever an *intermediate* model was
    refactored, for no consumer-visible change. Out for the same reason:
    ``rows``, ``bytes``, the data hash and ``generated_at``, which describe the
    bytes of one snapshot rather than the contract they were published under.

    It ships as ``contract_hash`` beside ``schema_version``, because the question
    a consumer actually asks is "is this the shape I validated?" — a hash answers
    it and cannot lie, where a major can and did.
    """
    payload = {
        "name": name,
        "tier": tier,
        "format": "csv",
        "dialect": CSV_DIALECT if dialect is None else dialect,
        "fields": [{"name": f["name"], "type": f["type"]} for f in fields],
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return "sha256:" + hashlib.sha256(canonical.encode()).hexdigest()


#: What gets published, and the contract each dataset publishes under (#385).
#:
#: The dataset LIST is deliberate config — publishing is a decision. Staging
#: datasets are the triage/lineage surface (spec § Catalog); conformed products
#: are what PM subscribes to. Lineage is not config: it comes from the dbt
#: manifest.
#:
#: The VERSIONS start where they do because #385's transition freezes each
#: dataset in place rather than renumbering. Until #385 a single module constant,
#: ``SCHEMA_VERSION``, was stamped onto whatever dataset minted next; carry-
#: forward plus skip-if-unchanged spread seven values across sixteen datasets, so
#: a major encoded when a dataset last minted rather than what its shape was.
#: The catalog-wide log that produced these numbers, kept because the archive
#: still carries them: 1.1.0/1.2.0 (#309) stg_wsl_committee_members identity
#: fields, then `assignments`, `roles` and `role_key`; 1.3.0 (#313) `roles` gained
#: `entity_id`; 1.4.0 (#313) every staging dataset gained `source` +
#: `resource_id`, and `stg_raw_fetches` + `citations` joined; 1.5.0 (#354)
#: `pm_anchors` joined; 1.6.0 (#357) each resource declares its `dialect`; 1.7.0
#: (#370) `assignments` gained `span_key`; 2.0.0 (#314) `pm_anchors` left.
#:
#: Two alternatives were rejected. Resetting every dataset to 1.0.0 DOWNGRADES
#: four of them on the wire, refusing for the consumer who had just re-pinned to
#: major 2. Renumbering everything up to a uniform 2.0.0 asserts a major change
#: for twelve datasets that had none — the exact sin #385 files — and re-mints
#: them to say it. So the starting values are arbitrary, and harmless: a major is
#: only ever compared WITHIN a dataset, and comparing two datasets' numbers was
#: never meaningful enough to buy with a wire break.
PUBLISHED_DATASETS: list[PublishedDataset] = [
    PublishedDataset(
        "stg_wsl_committees",
        "staging",
        (
            ContractRelease(
                "1.4.0",
                (
                    "biennium",
                    "committee_id",
                    "agency",
                    "name",
                    "long_name",
                    "acronym",
                    "phone",
                    "source",
                    "resource_id",
                ),
                "frozen in place at the #385 cutover: the version it was already publishing",
            ),
        ),
    ),
    PublishedDataset(
        "stg_wsl_sponsors",
        "staging",
        (
            ContractRelease(
                "1.4.0",
                (
                    "biennium",
                    "member_id",
                    "agency",
                    "name",
                    "long_name",
                    "first_name",
                    "last_name",
                    "party",
                    "district",
                    "source",
                    "resource_id",
                ),
                "frozen in place at the #385 cutover: the version it was already publishing",
            ),
        ),
    ),
    PublishedDataset(
        "stg_wsl_committee_members",
        "staging",
        (
            ContractRelease(
                "1.4.0",
                (
                    "biennium",
                    "committee_id",
                    "committee_agency",
                    "committee_name",
                    "member_id",
                    "name",
                    "long_name",
                    "first_name",
                    "last_name",
                    "agency",
                    "party",
                    "district",
                    "source",
                    "resource_id",
                ),
                "frozen in place at the #385 cutover: the version it was already publishing",
            ),
        ),
    ),
    PublishedDataset(
        "stg_wsl_meetings",
        "staging",
        (
            ContractRelease(
                "1.6.0",
                (
                    "meeting_window",
                    "meeting_agency",
                    "committee_id",
                    "committee_agency",
                    "committee_name",
                    "source",
                    "resource_id",
                ),
                "frozen in place at the #385 cutover: the version it was already publishing",
            ),
        ),
    ),
    PublishedDataset(
        "stg_roster_members",
        "staging",
        (
            ContractRelease(
                "1.4.0",
                (
                    "revision",
                    "district",
                    "chamber",
                    "year",
                    "order",
                    "name",
                    "party_token",
                    "annotation",
                    "source",
                    "resource_id",
                ),
                "frozen in place at the #385 cutover: the version it was already publishing",
            ),
        ),
    ),
    PublishedDataset(
        "stg_pdc_winners",
        "staging",
        (
            ContractRelease(
                "1.4.0",
                (
                    "chamber",
                    "election_year",
                    "person_id",
                    "filer_id",
                    "filer_name",
                    "party",
                    "legislative_district",
                    "office",
                    "general_election_status",
                    "candidacy_id",
                    "source",
                    "resource_id",
                ),
                "frozen in place at the #385 cutover: the version it was already publishing",
            ),
        ),
    ),
    PublishedDataset(
        "stg_sos_results",
        "staging",
        (
            ContractRelease(
                "1.4.0",
                (
                    "election_date",
                    "race",
                    "candidate",
                    "party",
                    "votes",
                    "percentage_of_total_votes",
                    "jurisdiction_name",
                    "source",
                    "resource_id",
                ),
                "frozen in place at the #385 cutover: the version it was already publishing",
            ),
        ),
    ),
    PublishedDataset(
        "stg_sos_filings",
        "staging",
        (
            ContractRelease(
                "1.4.0",
                (
                    "election_date",
                    "ballot_name",
                    "party_name",
                    "race_name",
                    "race_jurisdiction_name",
                    "source",
                    "resource_id",
                ),
                "frozen in place at the #385 cutover: the version it was already publishing",
            ),
        ),
    ),
    PublishedDataset(
        "stg_raw_fetches",
        "staging",
        (
            ContractRelease(
                "2.0.0",
                (
                    "source",
                    "resource_id",
                    "sha256",
                    "fetched_at",
                    "run_id",
                    "url",
                    "bytes",
                    "content_type",
                ),
                "frozen in place at the #385 cutover: the version it was already publishing",
            ),
        ),
    ),
    PublishedDataset(
        "person_crosswalk",
        "conformed",
        (
            ContractRelease(
                "2.0.0",
                (
                    "entity_id",
                    "natural_key",
                    "key_namespace",
                    "key_value",
                    "registered_by",
                    "merged_into",
                ),
                "frozen in place at the #385 cutover: the version it was already publishing",
            ),
        ),
    ),
    PublishedDataset(
        "org_crosswalk",
        "conformed",
        (
            ContractRelease(
                "1.2.0",
                (
                    "entity_id",
                    "natural_key",
                    "key_namespace",
                    "key_value",
                    "registered_by",
                    "merged_into",
                ),
                "frozen in place at the #385 cutover: the version it was already publishing",
            ),
        ),
    ),
    PublishedDataset(
        "persons",
        "conformed",
        (
            ContractRelease(
                "2.0.0",
                ("entity_id", "name_full", "name_source"),
                "frozen in place at the #385 cutover: the version it was already publishing",
            ),
        ),
    ),
    PublishedDataset(
        "organizations",
        "conformed",
        (
            ContractRelease(
                "1.0.0",
                (
                    "entity_id",
                    "name",
                    "long_name",
                    "acronym",
                    "agency",
                    "org_type",
                    "first_biennium",
                    "last_biennium",
                ),
                "frozen in place at the #385 cutover: the version it was already publishing",
            ),
        ),
    ),
    PublishedDataset(
        "assignments",
        "conformed",
        (
            ContractRelease(
                "1.7.0",
                (
                    "entity_id",
                    "member_id",
                    "source",
                    "role_key",
                    "span_kind",
                    "span_discriminator",
                    "span_start_biennium",
                    "span_end_biennium",
                    "valid_from",
                    "valid_to",
                    "is_active",
                    "span_key",
                ),
                "frozen in place at the #385 cutover: the version it was already publishing",
            ),
        ),
    ),
    PublishedDataset(
        "roles",
        "conformed",
        (
            ContractRelease(
                "1.3.0",
                (
                    "entity_id",
                    "role_key",
                    "role_type",
                    "name",
                    "span_kind",
                    "span_discriminator",
                    "org_source_id",
                    "org_entity_id",
                    "district",
                    "qualifier",
                ),
                "frozen in place at the #385 cutover: the version it was already publishing",
            ),
        ),
    ),
    # `internal` is not the subscriber contract (#313). It is published all the
    # same — same immutable version dirs, same digest, same `/datasets` tree,
    # because the deployment loads it exactly the way it loads every other
    # one — but nothing outside this repo is invited to depend on its shape, and
    # it carries no schema-stability promise. `citations` exists so
    # `/provenance/{type}/{id}` keeps answering once the Postgres provenance
    # tables retire; its columns follow the API, not consumers.
    #
    # It carries a version and a contract like every other dataset, and will bump
    # more often than any of them because of that. Deliberate: exempting the
    # internal tier from the gate would be a special case, and the tier is
    # already the signal that says not to bind here. That signal is the catalog's
    # own per-dataset `tier`, which `/health/datasets` already returns. There is
    # deliberately no second constant naming the internal tiers (CR 106): the one
    # place that would consume it is the API, which does not depend on this
    # package and should not start doing so — pulling dbt, duckdb and pandas into
    # the serving deployment to hold one frozenset would be a real cost for a
    # restatement of a field the catalog already publishes.
    PublishedDataset(
        "citations",
        "internal",
        (
            ContractRelease(
                "2.0.0",
                ("entity_type", "entity_id", "source", "resource_id"),
                "frozen in place at the #385 cutover: the version it was already publishing",
            ),
        ),
    ),
    # The `cutover` tier is EMPTY, and `test_the_cutover_tier_is_empty` keeps it
    # that way (#314). It carried exactly one dataset for its whole life:
    # `pm_anchors` (#354, power-map#495), the PM crosswalk seed, materialized
    # from Postgres by `anchor_export` rather than by a dbt model. power-map#525
    # re-keyed its assignment crosswalk off those Postgres ULIDs and onto the
    # published `assignments.span_key` (#370), and reported on #314 that it needs
    # no further seed — so the producer stops asserting the mapping instead of
    # shipping a frozen copy of it nightly.
    #
    # Delisting is the entire retraction: `catalog.json` is rebuilt from this list
    # every run, so the entry stops appearing tomorrow. The version dirs already
    # minted stay on disk and keep answering at their URLs — what retracts is the
    # forward assertion, not the archive.
    #
    # It also closes an ordering hazard this entry used to carry: the publisher
    # refuses a run whose table is missing, so dropping the `pm_*` columns while
    # this line stood would have wedged the nightly publish for every OTHER
    # dataset. With the line gone the hazard is gone with it, and #314's column
    # drop no longer has a publish-shaped tripwire in front of it.
]

#: The CSV serialisation every published dataset uses, declared rather than
#: left for a consumer to sniff (#357). These are duckdb ``COPY``'s defaults,
#: verified against its output rather than assumed — the point is that the
#: contract now says so out loud, in the datapackage a client already reads.
#:
#: It exists because the bytes are the product: two producers of the same rows
#: diverged on line endings alone (#354), which cost 12,462 bytes and a second,
#: conflicting sha256 for identical content. A digest is only meaningful once
#: the serialisation behind it is pinned.
#:
#: ``nullSequence`` is worth stating: duckdb writes NULL as a bare empty field
#: and an empty string as ``""``, so the two remain distinguishable on the wire.
CSV_DIALECT: dict[str, object] = {
    "delimiter": ",",
    "lineTerminator": "\n",
    "quoteChar": '"',
    "doubleQuote": True,
    "nullSequence": "",
    "header": True,
}

DEFAULT_MAX_SHRINK = 0.10

#: Where the built duckdb lives. The resolution was shared with `anchor_export`,
#: which materialized INTO the same file this reads FROM (CR 110) — two copies of
#: the literal would have let the pair drift silently, the export writing a table
#: the publisher never read while both jobs reported `ok`. #314 retired that
#: second caller; the helper stays because the explicit → env → default order is
#: the documented contract of `USA_WA_PIPELINE_DB`, not an implementation detail
#: of having had two callers.
PIPELINE_DB_ENV = "USA_WA_PIPELINE_DB"
_DEFAULT_PIPELINE_DB = "data/pipeline.duckdb"


def pipeline_db_path(explicit: str | Path | None) -> Path:
    """Resolve the pipeline duckdb: explicit flag, then env, then the default."""
    if explicit:
        return Path(explicit)
    return Path(os.environ.get(PIPELINE_DB_ENV, _DEFAULT_PIPELINE_DB))


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
    datasets: Sequence[PublishedDataset] | None = None,
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
        for dataset in datasets:
            name = dataset.name
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
            fields = [
                {"name": col[0], "type": _TYPE_MAP.get(col[1].split("(")[0], "string")}
                for col in columns
            ]
            fingerprint = contract_fingerprint(name=name, tier=dataset.tier, fields=fields)
            # The gate (#385): this dataset's published contract changed and its
            # version did not. Enforced ONE WAY — a change implies a bump — and
            # not as the "if and only if" the issue asks for, because the reverse
            # cannot hold: the published fields are `{name, type}` with no
            # descriptions and no prose, so a semantics-only change (a column
            # re-derived, its meaning shifted, its type unmoved) has no
            # fingerprint to move, and demanding the iff would forbid the honest
            # major. A pre-#385 entry records no contract at all; absence is not
            # a change, and it adopts the baseline on this run rather than
            # refusing sixteen datasets on the first night after deploy.
            #
            # Gated BEFORE the tmp dir exists, like the shrink gate above it: a
            # refusal must have nothing to strand inside the served tree (CR 15).
            if (
                prior
                and prior.get("contract_hash")
                and prior["contract_hash"] != fingerprint
                and prior.get("schema_version") == dataset.schema_version
            ):
                raise PublishRefused(
                    f"dataset {name!r}: its published contract changed while "
                    f"schema_version stayed {dataset.schema_version} "
                    f"({prior['contract_hash']} → {fingerprint}); a consumer pins "
                    "this number and has no other way to learn the shape moved — "
                    "append a ContractRelease to its PUBLISHED_DATASETS entry"
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
                    "dataset": dataset,
                    "name": name,
                    "tier": dataset.tier,
                    "tmp_dir": tmp_dir,
                    "rows": rows,
                    "hash": digest,
                    "bytes": csv_path.stat().st_size,
                    "fields": fields,
                    "contract_hash": fingerprint,
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
        # Mint on a change to the BYTES or to the CONTRACT they ship under (#385).
        # Hashing data.csv alone meant a metadata-only contract change never
        # reached a dataset at all: #357's `dialect` had to be declared by hand in
        # PIPELINE.md for every version dir that had not re-minted since. Under
        # per-dataset versions the bump is itself sometimes the only wire
        # difference, and an unpropagated one is a version nobody can read. The
        # cost is one version dir with a byte-identical data.csv per contract
        # change — rare by construction, and what makes a bump observable.
        unchanged = (
            prior
            and prior["hash"] == f"sha256:{item['hash']}"
            and prior.get("contract_hash") == item["contract_hash"]
            and prior.get("schema_version") == item["dataset"].schema_version
        )
        if unchanged:
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
            "schema_version": item["dataset"].schema_version,
            "contract_hash": item["contract_hash"],
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
                    "dialect": CSV_DIALECT,
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
                "schema_version": item["dataset"].schema_version,
                "contract_hash": item["contract_hash"],
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
    db_path = pipeline_db_path(ctx.args.db)
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
