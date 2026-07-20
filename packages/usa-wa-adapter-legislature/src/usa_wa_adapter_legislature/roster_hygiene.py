"""Sponsor-wire hygiene (#105) — committee-corroborated stale-row exclusion. Pure.

WSL's ``GetSponsors`` blanking is inconsistent: some departed members keep fully-named rows for
years (Kilduff left Dec 2020, still named through 2025-26; Senn and Nguyen resigned Jan 2025),
indistinguishable field-for-field from sitting members. The corroborating signal is the
committee-roster archive (#82): a departed member drops off every committee roster at the
departure boundary, while every sitting member — including a fresh mid-biennium appointee — is
committee-active (verified against 1999-00→2025-26; the Speaker sits on Rules).

:func:`stale_exclusions_by_biennium` is the consumer entry point. Two guardrails:

- **Coverage floor** (per biennium): when the committee cohort names fewer than ``min_coverage``
  of the wire's named members, that biennium contributes no exclusions — a thin/partial
  committee archive must not read as mass departure (the #44/#56 floor pattern; 1999-00's
  archive-floor coverage is ~79% and auto-skips, as does any pre-1999-00 biennium with no
  committee archive at all).
- **Tail rule** (cross-biennium): a member is excluded in biennium B only when they are
  committee-absent in B **and every later biennium** — a genuine departure is terminal, while a
  mid-tenure absence is a WSL archive gap (the audit's Shewmake case: a sitting 2019-2022 House
  member missing, by Id and name, from every archived House-era roster, yet committee-present
  after her 2023 Senate move). The rule also guarantees an exclusion only ever trims the *tail*
  of a tenure — it can never punch a mid-tenure hole, so no span is split into a new-start
  duplicate row (no superseded rows, no migration).

The sibling mover exclusion (#105 (a)) needs no external data and lives in
``usa_wa_adapter_pdc.normalize.pdc_matching.build_house_roster``.
"""

from __future__ import annotations

from typing import Any

from clearinghouse_core.logging import get_logger
from usa_wa_adapter_legislature.normalize.members import is_person

logger = get_logger(__name__)

#: Minimum fraction of a biennium's named sponsor rows the committee cohort must name for the
#: stale exclusion to run at all (the #44/#56 guardrail floor).
STALE_MIN_COVERAGE_DEFAULT = 0.9


def committee_member_ids_by_biennium(
    rosters: dict[tuple[str, str], list[dict[str, Any]]],
) -> dict[str, set[str]]:
    """Collapse the committee-member archive (``{(biennium, committee_id): [rows]}``, the
    :class:`CommitteeMemberCohortProvider.archived_rosters` shape) into the per-biennium
    committee-active member-id sets the exclusion consults. Ids stringified (the wire carries
    ints); id-less rows skipped."""
    ids: dict[str, set[str]] = {}
    for (biennium, _committee_id), rows in rosters.items():
        bucket = ids.setdefault(biennium, set())
        for row in rows:
            member_id = row.get("Id")
            if member_id is not None:
                bucket.add(str(member_id))
    return ids


def stale_member_ids(
    members: list[dict[str, Any]],
    committee_active_ids: set[str],
    *,
    biennium: str,
    min_coverage: float = STALE_MIN_COVERAGE_DEFAULT,
    log: bool = True,
) -> set[str]:
    """Named sponsor-row member ids absent from the biennium's committee rosters — stale
    *candidates* (#105 (b)); :func:`stale_exclusions_by_biennium` then applies the tail rule.
    Empty when the coverage guardrail trips. ``log=False`` defers the per-row exclusion log to
    the caller (which may rescue a candidate)."""
    named = {str(m["Id"]): m for m in members if is_person(m)}
    if not named:
        return set()
    if not committee_active_ids:
        # No committee archive for this biennium (pre-1999-00 floor / fresh deploy) — expected
        # every run, so quiet; the WARNING below is reserved for a *thin* cohort (partial pull).
        logger.debug("stale_exclusion_no_committee_data", extra={"biennium": biennium})
        return set()
    covered = set(named) & committee_active_ids
    coverage = len(covered) / len(named)
    if coverage < min_coverage:
        logger.warning(
            "stale_exclusion_skipped_low_coverage",
            extra={
                "biennium": biennium,
                "coverage": round(coverage, 3),
                "min_coverage": min_coverage,
                "named": len(named),
            },
        )
        return set()
    stale = set(named) - committee_active_ids
    if log:
        _log_exclusions(named, stale, biennium)
    return stale


def _log_exclusions(named: dict[str, dict[str, Any]], stale: set[str], biennium: str) -> None:
    """One operator-audit INFO line per excluded row (#105 verification surface)."""
    for member_id in sorted(stale):
        row = named.get(member_id, {})
        logger.info(
            "sponsor_stale_row_excluded",
            extra={
                "biennium": biennium,
                "member_id": member_id,
                "member_name": row.get("Name"),
                "agency": row.get("Agency"),
                "district": row.get("District"),
            },
        )


def stale_exclusions_by_biennium(
    members_by_biennium: dict[str, list[dict[str, Any]]],
    committee_ids_by_biennium: dict[str, set[str]],
    *,
    min_coverage: float = STALE_MIN_COVERAGE_DEFAULT,
) -> dict[str, set[str]]:
    """The consumer entry point: per-biennium stale exclusions with the **tail rule** applied.

    A per-biennium candidate (:func:`stale_member_ids`) is excluded only when the member has no
    committee presence in any *later* biennium either — later presence marks the absence as an
    archive gap, not a departure (see the module docstring). Biennium labels compare
    chronologically as strings (``YYYY-YY``)."""
    last_seen: dict[str, str] = {}
    for biennium, ids in committee_ids_by_biennium.items():
        for member_id in ids:
            if biennium > last_seen.get(member_id, ""):
                last_seen[member_id] = biennium
    exclusions: dict[str, set[str]] = {}
    for biennium, members in members_by_biennium.items():
        candidates = stale_member_ids(
            members,
            committee_ids_by_biennium.get(biennium, set()),
            biennium=biennium,
            min_coverage=min_coverage,
            log=False,
        )
        stale = {m for m in candidates if last_seen.get(m, "") <= biennium}
        rescued = candidates - stale
        if rescued:
            logger.info(
                "stale_exclusion_rescued_by_later_presence",
                extra={"biennium": biennium, "member_ids": sorted(rescued)},
            )
        named = {str(m["Id"]): m for m in members if is_person(m)}
        _log_exclusions(named, stale, biennium)
        exclusions[biennium] = stale
    return exclusions
