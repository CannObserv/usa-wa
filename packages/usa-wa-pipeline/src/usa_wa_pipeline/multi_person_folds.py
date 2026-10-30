"""Multi-person folds probe (#548): one roster identity whose rows belong to two people.

    python -m usa_wa_pipeline.multi_person_folds [--db PATH]

The roster carries no member ids, so the resolver mints one identity per name
fold, modulo the adjudication tables in ``roster_pdf/identity.py``. Two people
who print alike publish as one entity, and a new roster edition can fold two
together again with no gate noticing (#525 accepted limit 3). Two manual sweeps
found the two such folds there were: ``juriebsmith`` (#525) and ``tombrown``
(#548). This probe is those sweeps, nightly.

**The two shapes.** Each is a hit on one identity:

- ``after_death``: a row whose term began after a death recorded in the same
  identity. The death is the parsed ``deceased`` clause (``roster_pdf.succession``),
  never the printed wording, and bounds at the end of the window it states. An
  undated one bounds at the end of its row's term. A row's year is its
  term-start year, so only a later year is certainly after: a row of the death's
  own year may be the term he died in, and the death row itself is never a hit.
- ``mixed_honorific``: rows printed with a leading ``Mrs.`` beside rows without
  one. The roster prints a widow appointed to her husband's seat under his name
  (``identity.leads_with_widow_honorific``, the widow split's own rule).

**Over minted identities, not raw folds.** Rows are keyed by
``identity.identity_keys``, the resolver's rule: aliases group, and each side of
an adjudicated split is its own identity. So a split clears its hit, and
:data:`ACCEPTED` starts empty: a new multi-person fold fails with nothing to
hide behind. Its fix is a split plus ``adjudicate register --distinct-person``.

**Fails, after publish.** It runs in the nightly's probe loop, after publish, so
a hit emails the operator and never holds the catalog back: one wrong identity
is no reason to stop every other dataset, and its fix needs a human
adjudication either way. A judged false positive goes in :data:`ACCEPTED`, with
its evidence; the probe fails on a stale entry until it is removed.

Write-free, and reads only the built duckdb's ``stg_roster_members``.

Exit ``0`` clean · ``1`` an unaccepted hit or a stale acceptance, each named ·
``4`` degraded: the build holds no roster rows, or fewer identities carry a
parsed death than :data:`DEATH_IDENTITIES_FLOOR` — either would read as "no
hits" having compared less than the corpus holds.
"""

from __future__ import annotations

import argparse
import os
from collections import defaultdict
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any

import duckdb

from clearinghouse_core.job import JobContext, JobResult, run_job
from clearinghouse_core.logging import get_logger
from usa_wa_adapter_legislature.roster_pdf.audit import TERM_YEARS
from usa_wa_adapter_legislature.roster_pdf.identity import (
    IDENTITY_SPLITS,
    IDENTITY_WIDOW_SPLITS,
    identity_keys,
    leads_with_widow_honorific,
)
from usa_wa_adapter_legislature.roster_pdf.succession import parse_annotation

logger = get_logger(__name__)

#: Stable ledger identity (#178).
JOB_SLUG = "multi-person-folds"

_DEFAULT_DB = "data/pipeline.duckdb"

SHAPE_AFTER_DEATH = "after_death"
SHAPE_MIXED_HONORIFIC = "mixed_honorific"

_DECEASED = "deceased"

#: Identities carrying a parsed death on edition 2025-06-05 (#525's sweep, re-measured in
#: #548). The roster is cumulative, so an edition never has fewer, and a split never drops a
#: death row; fewer means a rewording the clause parser no longer reads, and the probe would
#: be blind to exactly those identities. Raise it when a new edition adds deaths.
DEATH_IDENTITIES_FLOOR = 103

#: An undated death's row in an unrecognised chamber bounds at the longest term: the
#: later bound, so the fewer rows it calls certainly after.
_LONGEST_TERM = max(TERM_YEARS.values())


@dataclass(frozen=True)
class Acceptance:
    """A hit judged legitimate: the identity's rows are one person's after all."""

    key: str
    shape: str
    reason: str
    evidence: str

    @property
    def label(self) -> str:
        return f"{self.key} {self.shape}"


#: Hits judged one person. Empty: both measured folds are split (#525, #548).
ACCEPTED: tuple[Acceptance, ...] = ()


@dataclass(frozen=True)
class Hit:
    """One identity holding rows of one of the two multi-person shapes."""

    key: str
    shape: str
    evidence: tuple[str, ...]

    @property
    def label(self) -> str:
        return f"{self.key} {self.shape}"

    def as_counter(self) -> dict[str, Any]:
        return {"key": self.key, "shape": self.shape, "evidence": list(self.evidence)}


def _describe(row: dict[str, Any]) -> str:
    return f"{row['year']} {row['chamber']} LD{row['district']} {row['name']!r}"


def death_bounds(row: dict[str, Any]) -> list[tuple[int, str]]:
    """``(last year the death can fall in, clause text)`` for each ``deceased``
    clause on the row: a stated window's end, else the end of the row's term."""
    bounds = []
    for clause in parse_annotation(row.get("annotation") or ""):
        if clause.verb != _DECEASED:
            continue
        if clause.parsed.window is not None:
            last = clause.parsed.window[1].year
        else:
            term = TERM_YEARS.get(str(row["chamber"]).lower(), _LONGEST_TERM)
            last = int(row["year"]) + term - 1
        bounds.append((last, clause.text))
    return bounds


def identities(
    roster: Iterable[dict[str, Any]],
    *,
    splits: Mapping[str, int] = IDENTITY_SPLITS,
    widow_splits: Mapping[str, str] = IDENTITY_WIDOW_SPLITS,
) -> dict[str, list[dict[str, Any]]]:
    """Identity key → its rows, keyed as the resolver mints them. Nameless rows drop."""
    named = [r for r in roster if isinstance(r.get("name"), str) and r["name"].strip()]
    keys = identity_keys(
        ((r["name"], int(r["year"])) for r in named), splits=splits, widow_splits=widow_splits
    )
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row, key in zip(named, keys, strict=True):
        grouped[key].append(row)
    return grouped


def after_death(key: str, rows: list[dict[str, Any]]) -> Hit | None:
    """The identity's rows listed in a year after one of its own deaths."""
    evidence: list[str] = []
    for index, row in enumerate(rows):
        for last, text in death_bounds(row):
            later = [
                other for j, other in enumerate(rows) if j != index and int(other["year"]) > last
            ]
            if later:
                evidence.append(f"death: {_describe(row)}: {text}")
                evidence.extend(_describe(other) for other in later)
    return Hit(key, SHAPE_AFTER_DEATH, tuple(dict.fromkeys(evidence))) if evidence else None


def mixed_honorific(key: str, rows: list[dict[str, Any]]) -> Hit | None:
    """The identity's leading-``Mrs.`` rows, when it also holds rows without one."""
    leading = [leads_with_widow_honorific(str(row["name"])) for row in rows]
    if not any(leading) or all(leading):
        return None
    return Hit(key, SHAPE_MIXED_HONORIFIC, tuple(_describe(row) for row in rows))


def find_hits(
    roster: list[dict[str, Any]],
    *,
    splits: Mapping[str, int] = IDENTITY_SPLITS,
    widow_splits: Mapping[str, str] = IDENTITY_WIDOW_SPLITS,
) -> tuple[list[Hit], dict[str, int]]:
    """``(hits, counters)`` over every identity the roster mints. Pure."""
    grouped = identities(roster, splits=splits, widow_splits=widow_splits)
    hits: list[Hit] = []
    deaths = 0
    for key, rows in sorted(grouped.items()):
        if any(death_bounds(row) for row in rows):
            deaths += 1
        hits.extend(hit for check in (after_death, mixed_honorific) if (hit := check(key, rows)))
    return hits, {"identities": len(grouped), "death_identities": deaths}


def run_probe(
    roster: list[dict[str, Any]],
    *,
    splits: Mapping[str, int] = IDENTITY_SPLITS,
    widow_splits: Mapping[str, str] = IDENTITY_WIDOW_SPLITS,
    accepted: Iterable[Acceptance] = ACCEPTED,
    death_floor: int | None = None,
) -> JobResult:
    """Find the hits and gate them against ``accepted``. Pure: the job does the read.

    ``death_floor`` defaults to :data:`DEATH_IDENTITIES_FLOOR`, read at call time."""
    if not roster:
        logger.warning("multi_person_folds_empty_roster_rows")
        return JobResult.degraded({"empty_roster_rows": True})
    hits, measured = find_hits(roster, splits=splits, widow_splits=widow_splits)
    floor = DEATH_IDENTITIES_FLOOR if death_floor is None else death_floor
    if measured["death_identities"] < floor:
        below = {"death_identities": measured["death_identities"], "death_identities_floor": floor}
        logger.warning("multi_person_folds_deaths_below_floor", extra=below)
        return JobResult.degraded(below)

    accepted_by_label = {a.label: a for a in accepted}
    hit_labels = {h.label for h in hits}
    counters: dict[str, Any] = {
        **measured,
        "hits": len(hits),
        "accepted": sorted(h.label for h in hits if h.label in accepted_by_label),
        "unaccepted_hits": [h.as_counter() for h in hits if h.label not in accepted_by_label],
        "stale_acceptances": sorted(set(accepted_by_label) - hit_labels),
    }
    counters["failures"] = [
        name for name in ("unaccepted_hits", "stale_acceptances") if counters[name]
    ]
    if counters["failures"]:
        logger.error("multi_person_folds_report", extra={"summary": counters})
        return JobResult.failed(counters, exit_code=1)
    logger.info("multi_person_folds_report", extra={"summary": counters})
    return JobResult.ok(counters)


def load_roster(db_path: str) -> list[dict[str, Any]]:
    """The staged roster, read back from the built duckdb."""
    con = duckdb.connect(db_path, read_only=True)
    try:
        return con.table("stg_roster_members").df().to_dict("records")
    finally:
        con.close()


def _add_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--db",
        default=None,
        help="Built pipeline duckdb (default: USA_WA_PIPELINE_DB, else data/pipeline.duckdb).",
    )


async def _probe_job(ctx: JobContext) -> JobResult:
    """Read the build's staged roster, then gate."""
    db_path = ctx.args.db or os.environ.get("USA_WA_PIPELINE_DB", _DEFAULT_DB)
    return run_probe(load_roster(db_path))


def main(argv: list[str] | None = None) -> int:
    """Flag a roster identity whose rows belong to two people."""
    return run_job(
        JOB_SLUG,
        _probe_job,
        argv=argv,
        prog="python -m usa_wa_pipeline.multi_person_folds",
        description="Write-free: flag a roster identity holding rows after its own death, "
        "or mixing a widow's 'Mrs.' rows with others.",
        extra_args=_add_args,
        commit=False,
        dry_run=False,
    )


if __name__ == "__main__":
    raise SystemExit(main())
