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
import json
import os
from datetime import date
from pathlib import Path
from typing import Any

from sqlalchemy import Table, delete, insert
from sqlalchemy.ext.asyncio import AsyncSession

from clearinghouse_core.job import JobContext, JobResult, run_job
from clearinghouse_core.logging import get_logger
from usa_wa_api.serving.schema import SCHEMA, SERVING_TABLES, ServingBase

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


def dataset_rows(root: Path, name: str, version: str) -> list[dict[str, str]]:
    """One published version's rows, verbatim — every value still a string."""
    path = Path(root) / name / version / "data.csv"
    with path.open(newline="") as handle:
        return list(csv.DictReader(handle))


def verify_contract(name: str, fields: list[dict[str, Any]], table: Table) -> list[str]:
    """Declared fields vs. the table's columns. Empty list = the contract holds.

    Both directions matter. A field the table has no column for means the
    publisher started asserting something this deployment would silently drop;
    a column no field fills means the API would answer nulls for a value the
    dataset used to carry. Either way the right move is to refuse and be told.
    """
    declared = {field["name"] for field in fields}
    columns = set(table.columns.keys())
    problems = []
    if extra := sorted(declared - columns):
        problems.append(
            f"{name}: datapackage declares {extra}, which `serving.{name}` has no column for"
        )
    if missing := sorted(columns - declared):
        problems.append(
            f"{name}: `serving.{name}` has columns {missing} the datapackage no longer declares"
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
    staged: dict[str, tuple[Table, list[dict[str, Any]]]] = {}
    for name in datasets:
        entry = entries.get(name)
        if entry is None:
            problems.append(
                f"{name}: not listed in catalog.json — the serving schema cannot be built"
            )
            continue
        version = entry["latest_version"]
        fields = datapackage_fields(root, name, version)
        table = SERVING_TABLES[name]
        problems.extend(verify_contract(name, fields, table))
        types = {field["name"]: field["type"] for field in fields}
        staged[name] = (
            table,
            [coerce_row(row, types) for row in dataset_rows(root, name, version)],
        )
    if problems:
        # Refuse before the first write: a partial load leaves the snapshot
        # internally inconsistent, which is worse than a stale consistent one.
        logger.error("serving_load_contract_mismatch", extra={"problems": problems})
        raise ContractMismatch("; ".join(problems))

    counters: dict[str, int] = {}
    for name, (table, rows) in staged.items():
        await session.execute(delete(table))
        if rows:
            await session.execute(insert(table), rows)
        counters[name] = len(rows)
    counters["versions"] = len({entries[name]["latest_version"] for name in datasets})
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
