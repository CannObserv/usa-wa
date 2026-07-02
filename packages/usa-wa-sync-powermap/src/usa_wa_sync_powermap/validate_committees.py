"""Read-only validation of the produced committee cohort against Power Map (#64,
sub-project 1).

Every WSL-produced org (``source='usa_wa_legislature'``) is a **read mirror** of PM
for its curated fields — ``Organization.name`` adopts PM's canonical name, and the
``organization_names`` / ``organization_acronyms`` child tables mirror PM's embedded
``names[]`` / ``acronyms[]`` (usa-wa#45/#47). This CLI verifies that mirror held: for
each PM-linked org it fetches the live ``OrgDetail`` (``get_entity``) and classifies
the org into a discrepancy bucket, distinguishing:

* **divergent** — local disagrees with PM's live value (a stale/broken mirror, or an
  org PM deleted/merged); actionable.
* **reconciled** — PM curated a rename (a ``former`` name window) and local mirrored
  it identically; the positive "PM-side change safely roundtripped" signal.
* **clean** — matches PM with no curation beyond what we produced.

Emit-nothing, read-only both sides: ``SELECT`` locally, ``get_entity`` on PM. Reads run
**sequentially** (~58 calls — naturally staggered, no concurrent flooding); each
``get_entity`` is wrapped in a bounded backoff on :class:`RetryableClientError` (the PM
client surfaces 429/5xx as that but does **not** retry itself — retry is the caller's
job). A global auth failure (:class:`DeliveryBlockedError`, 401/403) aborts fast.

Thin operator surface — ``python -m usa_wa_sync_powermap.validate_committees``; no
operator token (shell access is the trust boundary, as with the reconcile CLIs);
``--json`` for machine consumption. Exit codes mirror the reconcile family: ``0`` clean
· ``1`` divergences found · ``2`` auth block · ``3`` guardrail abort (empty cohort).

Known limitation — ``merged`` detection: PM's ``get_entity`` collapses a 404 to
``None`` without surfacing ``merged_into``, so a merged org currently reports
``missing_in_pm``. The classifier models ``merged`` (via :class:`PMTombstone`) so the
bucket is testable and ready if the client later exposes the tombstone; the live wiring
never produces it today.

Examples::

    python -m usa_wa_sync_powermap.validate_committees
    python -m usa_wa_sync_powermap.validate_committees --json
"""

import argparse
import asyncio
import json
import sys
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from clearinghouse_core.database import get_session_factory
from clearinghouse_core.logging import configure_logging, get_logger
from clearinghouse_core.provenance import FetchEvent
from clearinghouse_domain_legislative.identity import (
    Organization,
    OrganizationAcronym,
    OrganizationName,
)
from clearinghouse_sync_powermap.client import DeliveryBlockedError, RetryableClientError
from clearinghouse_sync_powermap.pmclient import GeneratedPowerMapClient
from usa_wa_sync_powermap.config import get_sidecar_settings

logger = get_logger(__name__)

#: Producer source whose cohort this validates.
_SOURCE = "usa_wa_legislature"
#: PM read path for orgs (embeds ``names[]`` / ``acronyms[]`` in the ``OrgDetail``).
READ_PATH = "/api/v1/orgs"

#: Discrepancy buckets.
ISSUE_UNLINKED = "unlinked"
ISSUE_MISSING = "missing_in_pm"
ISSUE_MERGED = "merged"
ISSUE_NAME_DRIFT = "name_drift"
ISSUE_ACRONYM_DRIFT = "acronym_drift"
ISSUE_NAMES_WINDOW_DRIFT = "names_window_drift"
ISSUE_ACRONYMS_DRIFT = "acronyms_drift"
ISSUE_PARENT_DRIFT = "parent_drift"

#: Foreground backoff schedule (seconds) — small, unlike the 60s-base outbox schedule
#: (`retry.backoff`), so a transient 429 doesn't stall an interactive read. Length is
#: the retry budget per call; exhausting it re-raises :class:`RetryableClientError`.
_BACKOFF_SECONDS = (1, 2, 4, 8)

#: Exit code for a guardrail abort (empty cohort) — distinct from a divergence (1).
EXIT_ABORTED = 3


# --- immutable snapshots (keep the classifier pure + unit-testable) -----------


@dataclass(frozen=True)
class NameWindow:
    """A dated name variant — the shared shape of a local ``OrganizationName`` row and
    a PM ``OrgName``. Compared by the full tuple so a field drift (type, canonical,
    window bounds) surfaces, keyed to PM's id via ``pm_org_name_id``."""

    name: str
    name_type: str
    is_canonical: bool
    effective_start: date | None
    effective_end: date | None
    pm_org_name_id: str | None

    def key(self) -> tuple:
        return (
            self.pm_org_name_id,
            (self.name or "").strip(),
            self.name_type,
            self.is_canonical,
            self.effective_start,
            self.effective_end,
        )


@dataclass(frozen=True)
class AcronymVariant:
    """An acronym variant — the shared shape of a local ``OrganizationAcronym`` and a
    PM ``OrgAcronym`` (``{acronym, is_canonical}``; no dated window)."""

    acronym: str
    is_canonical: bool
    pm_org_acronym_id: str | None

    def key(self) -> tuple:
        return (self.pm_org_acronym_id, (self.acronym or "").strip(), self.is_canonical)


@dataclass(frozen=True)
class LocalOrg:
    """Snapshot of a local ``Organization`` plus its mirrored child rows."""

    source_id: str
    name: str
    short_name: str | None
    acronym: str | None
    org_type: str
    pm_organization_id: str | None
    parent_pm_id: str | None
    name_windows: tuple[NameWindow, ...]
    acronym_variants: tuple[AcronymVariant, ...]


@dataclass(frozen=True)
class PMOrg:
    """Snapshot of PM's live ``OrgDetail``."""

    pm_id: str
    name: str | None
    parent_id: str | None
    names: tuple[NameWindow, ...]
    acronyms: tuple[AcronymVariant, ...]

    def canonical_acronym(self) -> str | None:
        for a in self.acronyms:
            if a.is_canonical:
                return a.acronym
        return None


@dataclass(frozen=True)
class PMTombstone:
    """A 404 that PM attributes to a merge (``merged_into`` known). Not produced by the
    live wiring today (see module docstring), but keeps ``merged`` testable."""

    merged_into: str | None


@dataclass(frozen=True)
class OrgReport:
    source_id: str
    pm_id: str | None
    issues: tuple[str, ...]
    reconciled: bool = False
    detail: dict = field(default_factory=dict)

    @property
    def divergent(self) -> bool:
        return bool(self.issues)


# --- pure mapping + classification --------------------------------------------


def _to_date(value: Any) -> date | None:
    if value is None or isinstance(value, date):
        return value
    return date.fromisoformat(value)


def pm_org_from_record(record: dict) -> PMOrg:
    """Map a PM ``OrgDetail`` dict onto a :class:`PMOrg` snapshot."""
    names = tuple(
        NameWindow(
            name=n["name"],
            name_type=n.get("name_type") or "legal",
            is_canonical=bool(n.get("is_canonical")),
            effective_start=_to_date(n.get("effective_start")),
            effective_end=_to_date(n.get("effective_end")),
            pm_org_name_id=str(n["id"]),
        )
        for n in record.get("names") or []
    )
    acronyms = tuple(
        AcronymVariant(
            acronym=a["acronym"],
            is_canonical=bool(a.get("is_canonical")),
            pm_org_acronym_id=str(a["id"]),
        )
        for a in record.get("acronyms") or []
    )
    parent = record.get("parent_id")
    return PMOrg(
        pm_id=str(record["id"]),
        name=record.get("name"),
        parent_id=str(parent) if parent is not None else None,
        names=names,
        acronyms=acronyms,
    )


def classify_org(local: LocalOrg, pm: PMOrg | PMTombstone | None) -> OrgReport:
    """Sort one org into its discrepancy bucket(s).

    Precedence: an unlinked / missing / merged org short-circuits (no field diff is
    meaningful). Otherwise accumulate every field-level drift. ``reconciled`` is set
    only on a fully-clean org that carries a PM-curated ``former`` name window — the
    positive roundtrip signal.
    """
    if local.pm_organization_id is None:
        return OrgReport(local.source_id, None, (ISSUE_UNLINKED,))
    if pm is None:
        return OrgReport(local.source_id, local.pm_organization_id, (ISSUE_MISSING,))
    if isinstance(pm, PMTombstone):
        return OrgReport(
            local.source_id,
            local.pm_organization_id,
            (ISSUE_MERGED,),
            detail={"merged_into": pm.merged_into},
        )

    issues: list[str] = []
    detail: dict = {}
    if (local.name or "").strip() != (pm.name or "").strip():
        issues.append(ISSUE_NAME_DRIFT)
        detail["name"] = {"local": local.name, "pm": pm.name}
    if (local.acronym or None) != (pm.canonical_acronym() or None):
        issues.append(ISSUE_ACRONYM_DRIFT)
        detail["acronym"] = {"local": local.acronym, "pm": pm.canonical_acronym()}
    if {w.key() for w in local.name_windows} != {w.key() for w in pm.names}:
        issues.append(ISSUE_NAMES_WINDOW_DRIFT)
        detail["names"] = {"local": len(local.name_windows), "pm": len(pm.names)}
    if {a.key() for a in local.acronym_variants} != {a.key() for a in pm.acronyms}:
        issues.append(ISSUE_ACRONYMS_DRIFT)
        detail["acronyms"] = {"local": len(local.acronym_variants), "pm": len(pm.acronyms)}
    if (local.parent_pm_id or None) != (pm.parent_id or None):
        issues.append(ISSUE_PARENT_DRIFT)
        detail["parent"] = {"local": local.parent_pm_id, "pm": pm.parent_id}

    reconciled = not issues and any(w.name_type == "former" for w in pm.names)
    return OrgReport(
        local.source_id,
        local.pm_organization_id,
        tuple(issues),
        reconciled=reconciled,
        detail=detail,
    )


# --- DB cohort load -----------------------------------------------------------


async def _load_cohort(session: AsyncSession) -> list[LocalOrg]:
    """Load every produced org plus its mirrored child rows as :class:`LocalOrg`
    snapshots. Includes unlinked rows (``pm_organization_id`` NULL) so they surface as
    :data:`ISSUE_UNLINKED` rather than being silently skipped."""
    orgs = list(
        (await session.execute(select(Organization).where(Organization.source == _SOURCE)))
        .scalars()
        .all()
    )
    # Resolve local parent id → parent's PM anchor, for the parent-drift compare.
    by_local_id = {o.id: o for o in orgs}
    snapshots: list[LocalOrg] = []
    for o in orgs:
        names = tuple(
            NameWindow(
                name=n.name,
                name_type=n.name_type,
                is_canonical=n.is_canonical,
                effective_start=n.effective_start,
                effective_end=n.effective_end,
                pm_org_name_id=str(n.pm_org_name_id) if n.pm_org_name_id else None,
            )
            for n in (
                await session.execute(
                    select(OrganizationName).where(OrganizationName.organization_id == o.id)
                )
            )
            .scalars()
            .all()
        )
        acronyms = tuple(
            AcronymVariant(
                acronym=a.acronym,
                is_canonical=a.is_canonical,
                pm_org_acronym_id=str(a.pm_org_acronym_id) if a.pm_org_acronym_id else None,
            )
            for a in (
                await session.execute(
                    select(OrganizationAcronym).where(OrganizationAcronym.organization_id == o.id)
                )
            )
            .scalars()
            .all()
        )
        parent_pm_id = None
        if o.parent_organization_id is not None:
            parent = by_local_id.get(o.parent_organization_id)
            if parent is not None and parent.pm_organization_id is not None:
                parent_pm_id = str(parent.pm_organization_id)
        snapshots.append(
            LocalOrg(
                source_id=o.source_id,
                name=o.name,
                short_name=o.short_name,
                acronym=o.acronym,
                org_type=o.org_type,
                pm_organization_id=str(o.pm_organization_id) if o.pm_organization_id else None,
                parent_pm_id=parent_pm_id,
                name_windows=names,
                acronym_variants=acronyms,
            )
        )
    return snapshots


async def _count_unbaselined(session: AsyncSession) -> int:
    """Count fetch events with a NULL ``content_hash`` (pre-#54 baseline gap)."""
    return int(
        (
            await session.execute(
                select(func.count())
                .select_from(FetchEvent)
                .where(FetchEvent.content_hash.is_(None))
            )
        ).scalar_one()
    )


# --- PM fetch with bounded backoff --------------------------------------------


async def _fetch_pm(pm_client: Any, read_path: str, pm_id: str, *, sleep=asyncio.sleep):
    """Fetch one PM entity, retrying :class:`RetryableClientError` (429/5xx) on the
    bounded :data:`_BACKOFF_SECONDS` schedule. Re-raises after the budget is spent so a
    persistent outage surfaces rather than silently under-reporting."""
    for delay in _BACKOFF_SECONDS:
        try:
            return await pm_client.get_entity(read_path, pm_id)
        except RetryableClientError:
            logger.warning("validate_pm_retry", extra={"pm_id": pm_id, "backoff_s": delay})
            await sleep(delay)
    return await pm_client.get_entity(read_path, pm_id)


# --- orchestrator -------------------------------------------------------------


async def validate_committees(session: AsyncSession, pm_client: Any) -> dict:
    """Diff every produced org against its live PM ``OrgDetail`` and tally the buckets.

    Empty cohort aborts (``empty_cohort``) — a zero-row local read is a bug, not an
    all-clean result. Returns a JSON-able summary; the caller drives the exit code off
    ``divergent`` / ``aborted``.
    """
    cohort = await _load_cohort(session)
    unbaselined = await _count_unbaselined(session)
    if not cohort:
        return {
            "checked": 0,
            "clean": 0,
            "reconciled": 0,
            "divergent": 0,
            "by_issue": {},
            "unbaselined_fetch_events": unbaselined,
            "aborted": "empty_cohort",
            "reports": [],
        }

    clean = reconciled = divergent = 0
    by_issue: dict[str, int] = {}
    reports: list[dict] = []
    for local in cohort:
        if local.pm_organization_id is None:
            report = classify_org(local, None)  # unlinked — no PM fetch
        else:
            record = await _fetch_pm(pm_client, READ_PATH, local.pm_organization_id)
            pm = pm_org_from_record(record) if record is not None else None
            report = classify_org(local, pm)
        if report.divergent:
            divergent += 1
            for issue in report.issues:
                by_issue[issue] = by_issue.get(issue, 0) + 1
            reports.append(
                {
                    "source_id": report.source_id,
                    "pm_id": report.pm_id,
                    "issues": list(report.issues),
                    "detail": report.detail,
                }
            )
        elif report.reconciled:
            reconciled += 1
        else:
            clean += 1

    return {
        "checked": len(cohort),
        "clean": clean,
        "reconciled": reconciled,
        "divergent": divergent,
        "by_issue": by_issue,
        "unbaselined_fetch_events": unbaselined,
        "aborted": None,
        "reports": reports,
    }


# --- CLI ----------------------------------------------------------------------


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m usa_wa_sync_powermap.validate_committees",
        description="Read-only local↔PM committee validation.",
    )
    parser.add_argument("--json", action="store_true", help="emit the summary as JSON")
    return parser


async def _run(args: argparse.Namespace) -> dict:
    """Open a session + PM client, run the validation, always close the client."""
    settings = get_sidecar_settings()
    if not settings.powermap_api_key:
        raise RuntimeError("POWERMAP_API_KEY is not set — cannot read from Power Map.")
    async with get_session_factory()() as session:
        pm_client = GeneratedPowerMapClient(
            base_url=settings.powermap_base_url, api_key=settings.powermap_api_key
        )
        try:
            return await validate_committees(session, pm_client)
        finally:
            await pm_client.aclose()


def main(argv: list[str] | None = None) -> int:
    configure_logging()
    args = _build_parser().parse_args(argv)
    try:
        summary = asyncio.run(_run(args))
    except DeliveryBlockedError as exc:
        print(json.dumps({"error": f"delivery blocked: {exc}"}))
        return 2
    print(json.dumps(summary, indent=None if args.json else 2, default=str))
    if summary.get("aborted"):
        return EXIT_ABORTED
    return 1 if summary.get("divergent") else 0


if __name__ == "__main__":
    sys.exit(main())
