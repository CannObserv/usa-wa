"""Shared spine for producer-side committee **rename** detection (#46 + #56).

A committee keeps a stable WSL ``Id`` while its name changes (usually at a biennium
boundary). PM models a name's validity as an ``OrgName`` window
(``[effective_start, effective_end)``, power-map#239); the #45 read mirror brings those
windows back. This module is the **write-side** spine both rename detectors share: given a
*current* and a *prior* ``{source_id: name}`` cohort, it diffs them on the stable id, runs
the guardrails, and emits the windowed dated-name evidence for each renamed body.

The two detectors differ only in how the cohorts are sourced and which local class they
govern:

- :mod:`reconcile_committee_names` (#46) — ``GetCommittees`` rosters, ``org_type='committee'``.
- :mod:`reconcile_committee_meeting_names` (#56) — ``GetCommitteeMeetings`` windows,
  ``org_type='other'`` (the Joint/`Other` class ``CommitteeService`` can't see, #39).

Both feed pre-built ``{source_id: name}`` maps here. The map **value is the name to both
diff and emit** — #46 feeds raw ``LongName``; #56 feeds the clean ``Name`` (#61
``observed_name``) so the double-prefixed ``LongName`` never reaches PM. Diffing the same
string we emit mirrors the match-and-observe-the-same-name principle.

Guardrails (all gate the whole run, returning a summary with ``aborted`` set):

- **Empty-pull.** Either cohort empty reads as a failed pull, never a real diff. Abort.
- **Low-overlap.** Too thin a shared-id overlap (measured against the *smaller* cohort so
  one-sided growth/drop reads 1.0) means a wrong-biennium pull / id-scheme change — a
  meaningless diff that would otherwise read as a clean "no renames". #46 defaults this to
  ``0.5`` (stable roster ids overlap near-totally); #56 relaxes it (meeting cohorts overlap
  thinly by dormancy) via ``min_overlap_fraction``.
- **Rename-storm floor.** A renamed fraction over ``max_rename_fraction`` reads as a
  normalisation artifact / wrong-biennium pull. The fraction is only applied once the
  overlap reaches ``storm_floor_min_overlap`` (a small overlap makes the fraction
  hair-trigger — one rename of two is 0.5); #56 raises this floor.

Per-row eligibility (skip + count): a renamed id absent from the live cohort — *hidden*
(archived/deleted but still produced; PM 422s evidence on an archived org) vs *unproduced*
(never produced, or owned by another source) — or one PM never anchored (can't attach by
id). Per-row PM rejections and transport blips are isolated; a global auth block
(``DeliveryBlockedError``) and real bugs propagate.

Emit-to-PM-only: PM curates ``is_canonical`` and the #45 read mirror brings the windows
back, so no local column is mutated and the caller need not commit.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from clearinghouse_core.logging import get_logger
from clearinghouse_domain_legislative.identity import Organization
from clearinghouse_domain_legislative.queries import live_only
from clearinghouse_sync_powermap.client import PayloadRejectedError
from clearinghouse_sync_powermap.descriptors import EntityDescriptor, normalize_name
from clearinghouse_sync_powermap.engine import TRANSIENT_EXCEPTIONS

logger = get_logger(__name__)

#: Producer source for WSL committees — scopes the cohort so a future committee-bearing
#: source isn't swept into the rename diff silently.
SOURCE = "usa_wa_legislature"
#: Rename-storm default: abort if more than this fraction of the overlapping cohort shows a
#: changed name. Real biennium renames are a handful; a third leaves headroom while still
#: catching a wrong-biennium pull or a normalisation artifact. Operator-overridable.
DEFAULT_MAX_RENAME_FRACTION = 0.34
#: Default low-overlap floor — a **low non-zero** sanity floor (not off): a fully-disjoint /
#: badly-wrong-biennium pull (overlap ≈ 0) still aborts rather than reading as a clean
#: "renamed: 0", while a dormancy-thinned meeting overlap passes. The strict roster sibling
#: (#46) overrides this upward (its stable rosters overlap near-totally). The param below
#: defaults to this so a caller can't omit it into a required-arg footgun.
DEFAULT_MIN_OVERLAP_FRACTION = 0.1
#: Per-row delivery failures isolated so one bad row doesn't abort the run.
#: ``DeliveryBlockedError`` (401/403) is deliberately **not** here — a global credential
#: failure aborts fast rather than failing every row.
_DELIVERY_FAILURES = TRANSIENT_EXCEPTIONS
#: Exit code for a guardrail abort — distinct from a partial row failure (1) so an
#: operator/cron can tell "took no action" from "acted, some failed".
EXIT_ABORTED = 3


async def live_cohort_by_source_id(
    session: AsyncSession, *, org_type: str
) -> dict[str, Organization]:
    """The live (not archived / deleted) produced rows of ``org_type``, keyed by
    ``source_id`` (the WSL ``Id``) for the rename join."""
    rows = (
        (
            await session.execute(
                live_only(
                    select(Organization).where(
                        Organization.source == SOURCE,
                        Organization.org_type == org_type,
                    ),
                    Organization,
                )
            )
        )
        .scalars()
        .all()
    )
    return {row.source_id: row for row in rows}


async def produced_source_ids(session: AsyncSession, *, org_type: str) -> set[str]:
    """All WSL-produced ``source_id``s of ``org_type`` regardless of liveness — used to
    classify a renamed id absent from the live cohort as *hidden* (archived/deleted) vs
    genuinely *unproduced* (never produced, or owned by another source)."""
    rows = (
        await session.execute(
            select(Organization.source_id).where(
                Organization.source == SOURCE,
                Organization.org_type == org_type,
            )
        )
    ).all()
    return {source_id for (source_id,) in rows}


def renamed_ids(current: dict[str, str], prior: dict[str, str]) -> list[str]:
    """Source ids present in **both** cohorts whose (normalized) name changed.

    ``normalize_name`` equality is the precision gate (same folding the org match cascade
    uses), so ``Ways & Means`` ⇄ ``Ways and Means`` is not a rename. Intersecting both
    cohorts means an id present in only one (a create, a #44 retirement, or — for the
    meeting class — dormancy) is never a rename."""
    overlap = current.keys() & prior.keys()
    return sorted(
        cid for cid in overlap if normalize_name(prior[cid]) != normalize_name(current[cid])
    )


async def _emit_names(
    descriptor: EntityDescriptor,
    pm_client: Any,
    row: Any,
    *,
    prior_name: str,
    new_name: str,
    boundary: Any,
    summary: dict,
    org_type: str,
) -> None:
    """Emit one dated-name observation for a renamed ``row``, tallying into ``summary``.

    Skips + counts an unanchored row (can't attach by id). Isolates a per-row PM rejection
    (422) and transport blip; a global auth block and real bugs propagate. On success
    increments ``emitted``. ``org_type`` rides every log line so the two detectors (#46
    ``committee`` / #56 ``other``) are distinguishable in aggregated logs."""
    if descriptor.anchor_value(row) is None:
        summary["skipped_unanchored"] += 1
        logger.warning(
            "reconcile_names_unanchored", extra={"source_id": row.source_id, "org_type": org_type}
        )
        return
    payload = descriptor.to_names_observation(
        row, prior_name=prior_name, new_name=new_name, boundary=boundary
    )
    try:
        result = await pm_client.post_observation(descriptor.observe_path, payload)
    except PayloadRejectedError as exc:
        summary["rejected"] += 1
        logger.warning(
            "reconcile_names_rejected",
            extra={"source_id": row.source_id, "org_type": org_type, "error": str(exc)},
        )
        return
    except _DELIVERY_FAILURES as exc:
        summary["failed"] += 1
        logger.warning(
            "reconcile_names_failed",
            extra={"source_id": row.source_id, "org_type": org_type, "error": repr(exc)},
        )
        return
    if result.anchored:
        summary["emitted"] += 1
    elif result.rejected:
        summary["rejected"] += 1
    else:
        summary["failed"] += 1
    logger.info(
        "reconcile_names_submitted",
        extra={
            "source_id": row.source_id,
            "org_type": org_type,
            "disposition": result.disposition,
        },
    )


async def reconcile_names_from_maps(
    session: AsyncSession,
    descriptor: EntityDescriptor,
    pm_client: Any,
    *,
    current: dict[str, str],
    prior: dict[str, str],
    biennium: str,
    prior_biennium: str,
    boundary: Any,
    org_type: str,
    dry_run: bool = False,
    max_rename_fraction: float = DEFAULT_MAX_RENAME_FRACTION,
    min_overlap_fraction: float = DEFAULT_MIN_OVERLAP_FRACTION,
    storm_floor_min_overlap: int = 0,
) -> dict:
    """Diff two ``{source_id: name}`` cohorts on the stable id and emit windowed dated-name
    evidence for each body whose (normalized) name changed.

    Guardrails (see module docstring): empty pull / low overlap / rename storm each gate the
    whole run (``aborted`` set, nothing emitted). A renamed id absent from the live cohort of
    ``org_type`` is counted-skipped (*hidden* vs *unproduced*); unanchored ones are counted
    and skipped. Per-row blips/rejections isolated; a global auth block propagates.
    ``dry_run`` runs the diff and guards but posts nothing. Emit-to-PM-only — no local write,
    no commit needed. ``emitted`` counts observations accepted this run (idempotent re-emits
    included)."""
    overlap = current.keys() & prior.keys()
    renamed = renamed_ids(current, prior)
    summary = {
        "biennium": biennium,
        "prior_biennium": prior_biennium,
        "current": len(current),
        "prior": len(prior),
        "overlap": len(overlap),
        "renamed": len(renamed),
        "emitted": 0,
        "skipped_unanchored": 0,
        "skipped_hidden": 0,
        "skipped_unproduced": 0,
        "rejected": 0,
        "failed": 0,
        "dry_run": dry_run,
        "aborted": None,
    }
    if not current or not prior:
        # Either side empty ⇒ a failed pull, never a real diff (an empty current windows
        # nothing; an empty prior makes every committee look brand-new and mis-windows).
        summary["aborted"] = "empty_pull"
        logger.warning(
            "reconcile_names_aborted",
            extra={"reason": "empty_pull", "biennium": biennium, "org_type": org_type},
        )
        return summary
    # Denominator is the SMALLER cohort: "of the smaller cohort, what fraction overlapped."
    # Reads 1.0 whenever one cohort contains the other (pure growth *or* pure drop) and only
    # drops when the cohorts genuinely diverge — the wrong-biennium case.
    if len(overlap) / min(len(current), len(prior)) < min_overlap_fraction:
        summary["aborted"] = "low_overlap"
        logger.warning(
            "reconcile_names_aborted",
            extra={
                "reason": "low_overlap",
                "overlap": len(overlap),
                "current": len(current),
                "prior": len(prior),
                "min_overlap_fraction": min_overlap_fraction,
                "org_type": org_type,
            },
        )
        return summary
    if len(overlap) >= max(1, storm_floor_min_overlap) and (
        len(renamed) / len(overlap) > max_rename_fraction
    ):
        # Mass rename ⇒ suspect normalisation artifact / wrong-biennium pull. Only weighed
        # once the overlap is large enough that a fraction is meaningful.
        summary["aborted"] = "rename_storm"
        logger.warning(
            "reconcile_names_aborted",
            extra={
                "reason": "rename_storm",
                "renamed": len(renamed),
                "overlap": len(overlap),
                "max_rename_fraction": max_rename_fraction,
                "org_type": org_type,
            },
        )
        return summary
    if dry_run:
        return summary
    cohort = await live_cohort_by_source_id(session, org_type=org_type)
    produced = await produced_source_ids(session, org_type=org_type)
    for cid in renamed:
        row = cohort.get(cid)
        if row is None:
            # Absent from the live cohort: hidden (archived/deleted but still produced) vs
            # genuinely unproduced (never produced, or owned by another source).
            if cid in produced:
                summary["skipped_hidden"] += 1
                logger.warning(
                    "reconcile_names_hidden", extra={"source_id": cid, "org_type": org_type}
                )
            else:
                summary["skipped_unproduced"] += 1
                logger.warning(
                    "reconcile_names_unproduced", extra={"source_id": cid, "org_type": org_type}
                )
            continue
        await _emit_names(
            descriptor,
            pm_client,
            row,
            prior_name=prior[cid],
            new_name=current[cid],
            boundary=boundary,
            summary=summary,
            org_type=org_type,
        )
    return summary
