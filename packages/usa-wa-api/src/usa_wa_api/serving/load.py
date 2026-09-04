"""The serving loader (#313): published datasets → the `serving` schema.

    python -m usa_wa_api.serving.load [--root DIR] [--json]

This is where the deployment becomes **the first consumer of its own datapackage
contract** (spec § Transition plan step 8). It reads what ``catalog.json`` says
is current — never a path it guesses — and refuses any dataset whose declared
fields no longer match the table they load into.

Three properties, each deliberate:

- **The catalog is the index.** Version dirs are immutable and kept forever, so
  the newest one on disk is not necessarily the published one; only the catalog
  says which is. Guessing would silently serve a superseded or half-written
  snapshot.
- **One transaction, one decision.** Every dataset is replaced or none is.
  Refusing one while committing the rest would leave assignments pointing at a
  person table that was not refreshed — an internally inconsistent snapshot is
  worse than a stale consistent one, which is exactly why the publisher's own
  gates work the same way.
- **Replacement, not merge.** A dataset version *is* the whole live set, so a
  row the publisher stopped asserting has to disappear here too
  (retraction-as-absence, the #302 publication contract).
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import Table, delete, insert
from sqlalchemy.ext.asyncio import AsyncSession

from clearinghouse_core.job import JobContext, JobResult, run_job
from clearinghouse_core.logging import get_logger
from usa_wa_api.serving.schema import SCHEMA, SERVING_TABLES, LoadState, ServingBase

logger = get_logger(__name__)

#: Stable ledger identity (#178).
JOB_SLUG = "serving-load"

DATASETS_ROOT_ENV = "USA_WA_DATASETS_ROOT"
_DEFAULT_ROOT = "data/datasets"

#: Datasets the API serves from. Every one must be in the catalog: the serving
#: schema is rebuildable from ``published/`` alone, so a missing dataset is a
#: refusal rather than an empty table answering 200 with nothing in it.
SERVED_DATASETS: tuple[str, ...] = tuple(SERVING_TABLES)


class ContractMismatch(RuntimeError):
    """The published contract and the serving schema disagree — load nothing."""


@dataclass(frozen=True)
class _Staged:
    """One dataset read and coerced, waiting for every other one to pass too."""

    table: Table
    version: str
    sha256: str | None
    rows: list[dict[str, Any]]


def datasets_root() -> Path:
    """The publisher's output tree, the same root ``/datasets/*`` serves from."""
    return Path(os.environ.get(DATASETS_ROOT_ENV, _DEFAULT_ROOT))


def catalog_entries(root: Path) -> dict[str, dict[str, Any]]:
    """``name → catalog entry`` for what is published now.

    An unpublished box returns ``{}`` rather than raising: absence is a finding
    the caller reports (the #180 posture), not a crash.
    """
    catalog_path = Path(root) / "catalog.json"
    if not catalog_path.is_file():
        return {}
    catalog = json.loads(catalog_path.read_text())
    return {entry["name"]: entry for entry in catalog.get("datasets", [])}


def datapackage_fields(root: Path, name: str, version: str) -> list[dict[str, Any]]:
    """The declared schema fields of one published version."""
    path = Path(root) / name / version / "datapackage.json"
    descriptor = json.loads(path.read_text())
    return list(descriptor["resources"][0]["schema"]["fields"])


def datapackage_resource(root: Path, name: str, version: str) -> dict[str, Any]:
    """One published version's resource descriptor — schema plus `hash`/`rows`."""
    path = Path(root) / name / version / "datapackage.json"
    return dict(json.loads(path.read_text())["resources"][0])


def read_dataset(root: Path, name: str, version: str) -> tuple[list[str], list[dict[str, str]]]:
    """``(header, rows)`` of one published version — every value still a string.

    The header comes back because the CSV has to be checked against the
    datapackage that describes it, not only against the table (CR 93).
    """
    path = Path(root) / name / version / "data.csv"
    with path.open(newline="") as handle:
        reader = csv.DictReader(handle)
        return list(reader.fieldnames or []), list(reader)


def dataset_rows(root: Path, name: str, version: str) -> list[dict[str, str]]:
    """One published version's rows, verbatim — every value still a string."""
    return read_dataset(root, name, version)[1]


def verify_payload(root: Path, name: str, version: str, resource: dict[str, Any]) -> list[str]:
    """The published bytes against the digest and row count that describe them.

    The datapackage carries a ``sha256`` of ``data.csv`` for exactly this, and a
    loader that reads the bytes while ignoring the digest is trusting a contract
    rather than verifying one (CR 91). Truncation is the failure that matters:
    it exits 0 everywhere else, so the nightly's ``OnFailure=`` never fires and
    a short table just looks like a quiet day. ``rows`` is checked alongside
    because it costs nothing here and names that cause far better than a digest
    mismatch does.
    """
    path = Path(root) / name / version / "data.csv"
    problems: list[str] = []
    declared_hash = resource.get("hash")
    if declared_hash:
        digest = f"sha256:{hashlib.sha256(path.read_bytes()).hexdigest()}"
        if digest != declared_hash:
            problems.append(
                f"{name}: data.csv sha256 is {digest}, datapackage declares {declared_hash} — "
                "the published bytes are not the bytes on disk"
            )
    declared_rows = resource.get("rows")
    if declared_rows is not None:
        actual = len(dataset_rows(root, name, version))
        if actual != declared_rows:
            problems.append(
                f"{name}: data.csv holds {actual} rows, datapackage declares {declared_rows}"
            )
    return problems


def verify_header(name: str, header: list[str], fields: list[dict[str, Any]]) -> list[str]:
    """The CSV's own header against the fields its datapackage declares (CR 93).

    Compared as sets: the loader keys rows by name, never by position, so a
    reordering is not a contract break. A **missing** column is the silent one —
    ``csv.DictReader`` simply omits the key, SQLAlchemy compiles the INSERT from
    the keys present, and that column loads NULL for every row with no error. An
    extra column fails loudly on its own, but is named here for symmetry.
    """
    declared = {field["name"] for field in fields}
    present = set(header)
    problems = []
    if missing := sorted(declared - present):
        problems.append(
            f"{name}: data.csv header is missing {missing}, which its datapackage declares"
        )
    if extra := sorted(present - declared):
        problems.append(
            f"{name}: data.csv header carries {extra}, which its datapackage does not declare"
        )
    return problems


def verify_contract(name: str, fields: list[dict[str, Any]], table: Table) -> list[str]:
    """Declared fields vs. the table's columns. Empty list = the contract holds.

    Both directions matter. A field the table has no column for means the
    publisher started asserting something this deployment would silently drop;
    a column no field fills means the API would answer nulls for a value the
    dataset used to carry. Either way the right move is to refuse and be told.
    """
    declared = {field["name"] for field in fields}
    columns = set(table.columns.keys())
    # The dataset's published name and the table's are not always the same — a
    # `stg_` prefix names a pipeline tier, which is not a fact about the table
    # the API reads — so the message says both rather than assuming one.
    target = f"serving.{table.name}"
    problems = []
    if extra := sorted(declared - columns):
        problems.append(f"{name}: datapackage declares {extra}, which `{target}` has no column for")
    if missing := sorted(columns - declared):
        problems.append(
            f"{name}: `{target}` has columns {missing} the datapackage no longer declares"
        )
    return problems


def _coerce(value: str | None, field_type: str) -> Any:
    """One CSV cell → the type the datapackage declares for its column."""
    if value is None or value == "":
        # Every published column round-trips absence as an empty cell, so ""
        # is "nothing known", never the empty string. Answering "" where the
        # dataset says nothing would invent a value.
        return None
    if field_type == "date":
        return date.fromisoformat(value)
    if field_type == "boolean":
        return value.strip().lower() in {"true", "t", "1", "yes"}
    if field_type == "integer":
        return int(value)
    return value


def coerce_row(row: dict[str, str | None], types: dict[str, str]) -> dict[str, Any]:
    """A CSV row → typed values, driven by the datapackage's own declarations.

    Contract-driven rather than guessed per column name: the publisher says
    what each column means, and this is the deployment taking it at its word.
    """
    return {name: _coerce(value, types.get(name, "string")) for name, value in row.items()}


async def load_serving(
    session: AsyncSession,
    root: Path,
    *,
    datasets: tuple[str, ...] = SERVED_DATASETS,
) -> dict[str, int]:
    """Replace the whole serving snapshot from the published tree. Returns row counts.

    Raises :class:`ContractMismatch` before writing anything when a dataset is
    absent from the catalog or its declared fields disagree with the table.
    """
    entries = catalog_entries(root)
    problems: list[str] = []
    staged: dict[str, _Staged] = {}
    for name in datasets:
        entry = entries.get(name)
        if entry is None:
            problems.append(
                f"{name}: not listed in catalog.json — the serving schema cannot be built"
            )
            continue
        version = entry["latest_version"]
        resource = datapackage_resource(root, name, version)
        fields = list(resource["schema"]["fields"])
        table = SERVING_TABLES[name]
        # Three checks, in widening order: the bytes are the published bytes,
        # the CSV matches the datapackage describing it, and the datapackage
        # matches the table it loads into.
        problems.extend(verify_payload(root, name, version, resource))
        header, raw_rows = read_dataset(root, name, version)
        problems.extend(verify_header(name, header, fields))
        problems.extend(verify_contract(name, fields, table))
        types = {field["name"]: field["type"] for field in fields}
        staged[name] = _Staged(
            table=table,
            version=version,
            sha256=resource.get("hash"),
            rows=[coerce_row(row, types) for row in raw_rows],
        )
    if problems:
        # Refuse before the first write: a partial load leaves the snapshot
        # internally inconsistent, which is worse than a stale consistent one.
        logger.error("serving_load_contract_mismatch", extra={"problems": problems})
        raise ContractMismatch("; ".join(problems))

    loaded_at = datetime.now(UTC)
    counters: dict[str, int] = {}
    for name, item in staged.items():
        await session.execute(delete(item.table))
        if item.rows:
            await session.execute(insert(item.table), item.rows)
        # Same transaction as the rows it describes: a state row that could
        # outlive its data would be worse than no state row at all.
        await session.execute(delete(LoadState.__table__).where(LoadState.dataset == name))
        await session.execute(
            insert(LoadState.__table__),
            [
                {
                    "dataset": name,
                    "version": item.version,
                    "sha256": item.sha256,
                    "rows": len(item.rows),
                    "loaded_at": loaded_at,
                }
            ],
        )
        counters[name] = len(item.rows)
    logger.info("serving_load_complete", extra=dict(counters))
    return counters


async def create_serving_tables(session: AsyncSession) -> None:
    """Create any missing serving tables. Idempotent, and NOT a migration —
    see :mod:`usa_wa_api.serving.schema` on why this tier owns no state.

    The **schema** is not created here. ``CREATE SCHEMA`` needs CREATE on the
    database, which the app role does not have and should not — that is the #22
    DDL/DML role split. Postgres checks that privilege *before* ``IF NOT EXISTS``
    short-circuits, so even a no-op call fails as the app role (verified against
    prod). ``scripts/grants.sql`` provisions the schema under the owner role and
    grants the app CREATE *inside* it; the app then owns every table it builds
    there, which is what makes drop-and-rebuild its own to do.
    """
    connection = await session.connection()
    await connection.run_sync(ServingBase.metadata.create_all)


async def ensure_serving_schema(session: AsyncSession) -> None:
    """Create the schema itself — owner privileges only; see :func:`create_serving_tables`.

    Production provisions it through ``scripts/grants.sql``. This exists for
    contexts that own their database outright (the test harness), so a suite can
    stand the tier up without a privileged deploy step.
    """
    connection = await session.connection()
    await connection.exec_driver_sql(f'CREATE SCHEMA IF NOT EXISTS "{SCHEMA}"')


def _add_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--root", default=None, help=f"Published datasets root (default {DATASETS_ROOT_ENV})."
    )


async def _load_job(ctx: JobContext) -> JobResult:
    session = ctx.require_session()
    root = Path(ctx.args.root) if ctx.args.root else datasets_root()
    await create_serving_tables(session)
    if not catalog_entries(root):
        # Nothing published yet is a degraded run, not a failure: a fresh box
        # has no catalog and the API answers empty until the first publish.
        logger.warning("serving_load_unpublished", extra={"root": str(root)})
        return JobResult.degraded({"published": False})
    return JobResult.ok(await load_serving(session, root))


def main(argv: list[str] | None = None) -> int:
    """Load the published datasets into the serving schema."""
    return run_job(
        JOB_SLUG,
        _load_job,
        argv=argv,
        prog="python -m usa_wa_api.serving.load",
        description="Published datasets → the disposable Postgres serving schema (#313).",
        extra_args=_add_args,
    )


if __name__ == "__main__":
    raise SystemExit(main())
