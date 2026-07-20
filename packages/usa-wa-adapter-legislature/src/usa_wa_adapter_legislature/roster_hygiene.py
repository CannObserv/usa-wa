"""Sponsor-wire hygiene (#105) — committee-corroborated stale-row exclusion. Pure.

WSL's ``GetSponsors`` blanking is inconsistent: some departed members keep fully-named rows for
years (Kilduff left Dec 2020, still named through 2025-26; Senn and Nguyen resigned Jan 2025),
indistinguishable field-for-field from sitting members. The corroborating signal is the
committee-roster archive (#82): a departed member drops off every committee roster at the
departure boundary, while every sitting member — including a fresh mid-biennium appointee — is
committee-active (verified against 1999-00→2025-26; the Speaker sits on Rules).

:func:`stale_member_ids` applies that rule per biennium, guarded by a **coverage floor**: when
the biennium's committee cohort names fewer than ``min_coverage`` of the wire's named members,
the exclusion is skipped entirely — a thin/partial committee archive must not read as mass
departure (the #44/#56 floor pattern; 1999-00's archive-floor coverage is ~79% and auto-skips,
as does any pre-1999-00 biennium with no committee archive at all).

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
) -> set[str]:
    """Named sponsor-row member ids absent from the biennium's committee rosters — the
    presumed-departed stale rows (#105 (b)). Empty when the coverage guardrail trips."""
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
    for member_id in sorted(stale):
        row = named[member_id]
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
    return stale
