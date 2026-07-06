"""Write-free probe: is the WSL member ``Id`` a stable ``Person.source_id``? (P1b step 0)

Before ingesting the member cluster (persons, party, seats, committee memberships) we
must know whether the WSL member ``Id`` can key a ``Person`` — the same value for a
person **across endpoints** (does ``SponsorService.GetSponsors`` agree with
``CommitteeService.GetActiveCommitteeMembers`` on a shared person's ``Id``?) and
**across bienniums** (does a re-elected member keep the same ``Id`` from ``2023-24`` to
``2025-26``?). If ``Id`` is stable, the normalizers key ``Person`` on ``Id`` directly;
if it diverges cross-endpoint, the committee normalizer must instead name-match its
members against the sponsor cohort.

**Read-only, no archival.** Like :mod:`probe_committee_extent`, it talks to
:class:`~usa_wa_adapter_legislature.transport.WSLClient` directly — **not** the
:class:`AdapterRunner` — so nothing writes a ``FetchEvent`` / ``RawPayload``. The
matching is by name (``LastName``, ``FirstName``) so it can *detect* an ``Id`` mismatch
independently of the very ``Id`` under test.

``GetSponsors`` returns **one row per (member, chamber-tenure)**, so a member appears
once per tenure under a stable ``Id``. Superseded / departed tenures come back as a
**name-blanked stub** — a real ``Id`` with a chamber-typed ``LongName`` (``"Senator "``)
but the name, ``District`` and ``Party`` stripped. :func:`is_person` filters those stubs
so they don't pollute the overlap tally (and flags the count the real Person normalizer
will likewise skip, deduping the surviving named rows by ``Id``).

    python -m usa_wa_adapter_legislature.probe_member_identity
    python -m usa_wa_adapter_legislature.probe_member_identity --biennium 2025-26 --json
"""

import argparse
import asyncio
import json
import sys
from datetime import UTC, datetime
from typing import Any

from clearinghouse_core.logging import configure_logging, get_logger
from usa_wa_adapter_legislature.normalize.members import is_person
from usa_wa_adapter_legislature.refresh import biennium_for_date, previous_biennium
from usa_wa_adapter_legislature.transport import WSLClient

logger = get_logger(__name__)

# ``is_person`` (imported from normalize.members, the single source of truth) filters the
# name-blanked stubs GetSponsors returns for a superseded/departed (member, chamber-tenure)
# — the same predicate the real Person normalizer skips on.

#: Default number of active committees to sample for the cross-endpoint check. A dozen
#: rosters cover ~100 distinct members — plenty to detect an Id re-key without pulling
#: every committee.
DEFAULT_COMMITTEE_SAMPLE = 12


def name_key(member: dict[str, Any]) -> tuple[str, str]:
    """Endpoint/biennium-independent identity key: ``(last, first)``, case/space-normalized.

    Deliberately excludes ``Id`` (the value under test) and ``District`` (which can move
    under redistricting between bienniums), so the key can flag an ``Id`` divergence for
    the same human.
    """
    return (
        (member.get("LastName") or "").strip().lower(),
        (member.get("FirstName") or "").strip().lower(),
    )


def compare_id_stability(
    cohort_a: list[dict[str, Any]], cohort_b: list[dict[str, Any]]
) -> dict[str, Any]:
    """Match two member cohorts by :func:`name_key` and tally ``Id`` agreement.

    ``stable`` is True only when there is a non-empty overlap **and** every matched
    person carries the same ``Id`` in both cohorts — an empty overlap is *not* vacuously
    stable (no evidence either way). ``divergences`` lists every same-name / different-Id
    pair (the re-key signal).
    """
    a = {name_key(m): m for m in cohort_a}
    b = {name_key(m): m for m in cohort_b}
    shared = sorted(set(a) & set(b))
    divergences: list[dict[str, Any]] = []
    same = 0
    for k in shared:
        id_a, id_b = a[k].get("Id"), b[k].get("Id")
        if id_a is not None and id_b is not None and int(id_a) == int(id_b):
            same += 1
        else:
            divergences.append(
                {
                    "name": f"{k[1]} {k[0]}".title(),
                    "id_a": id_a,
                    "id_b": id_b,
                    "district_a": a[k].get("District"),
                    "district_b": b[k].get("District"),
                }
            )
    return {
        "matched": len(shared),
        "same_id": same,
        "diff_id": len(divergences),
        "only_a": len(set(a) - set(b)),
        "only_b": len(set(b) - set(a)),
        "stable": len(shared) > 0 and not divergences,
        "divergences": divergences,
    }


async def probe_member_identity(
    sponsor_client: Any,
    committee_client: Any,
    *,
    biennium: str,
    prior_biennium: str,
    committee_sample: int = DEFAULT_COMMITTEE_SAMPLE,
) -> dict[str, Any]:
    """Run both stability checks and return a JSON-able verdict.

    Cross-endpoint: ``GetSponsors(biennium)`` persons vs the members of up to
    ``committee_sample`` active committees. Cross-biennium: ``GetSponsors(biennium)`` vs
    ``GetSponsors(prior_biennium)``. ``id_is_stable_source_id`` is True only when *both*
    checks are stable; it picks the canonical ``source_id`` accordingly.
    """
    current = await sponsor_client.get_sponsors(biennium)
    prior = await sponsor_client.get_sponsors(prior_biennium)
    cur_persons = [m for m in current if is_person(m)]
    pri_persons = [m for m in prior if is_person(m)]

    active = (await committee_client.fetch_active_committees()).records
    committee_members: list[dict[str, Any]] = []
    sampled: list[dict[str, Any]] = []
    for committee in active[:committee_sample]:
        agency, name = committee.get("Agency"), committee.get("Name")
        if not agency or not name:
            continue
        members = await committee_client.get_active_committee_members(agency, name)
        persons = [m for m in members if is_person(m)]
        committee_members.extend(persons)
        sampled.append({"agency": agency, "name": name, "members": len(persons)})

    cross_endpoint = compare_id_stability(cur_persons, committee_members)
    cross_biennium = compare_id_stability(cur_persons, pri_persons)
    stable = cross_endpoint["stable"] and cross_biennium["stable"]

    logger.info(
        "probe_member_identity",
        extra={
            "cross_endpoint_stable": cross_endpoint["stable"],
            "cross_biennium_stable": cross_biennium["stable"],
            "id_is_stable_source_id": stable,
        },
    )

    return {
        "biennium": biennium,
        "prior_biennium": prior_biennium,
        "sponsor_counts": {
            "total": len(current),
            "persons": len(cur_persons),
            "non_person": len(current) - len(cur_persons),
        },
        "prior_sponsor_counts": {"total": len(prior), "persons": len(pri_persons)},
        "committees_sampled": sampled,
        "cross_endpoint": cross_endpoint,
        "cross_biennium": cross_biennium,
        "id_is_stable_source_id": stable,
        "recommended_source_id": (
            "GetSponsors.Id"
            if stable
            else "(FirstName,LastName,District) name-match against the sponsor cohort"
        ),
    }


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m usa_wa_adapter_legislature.probe_member_identity",
        description="Write-free probe of WSL member Id stability (cross-endpoint + biennium).",
    )
    parser.add_argument(
        "--biennium", default=None, help="current label (default: current from date)"
    )
    parser.add_argument(
        "--prior-biennium",
        default=None,
        help="prior label to compare (default: the biennium before --biennium)",
    )
    parser.add_argument("--committee-sample", type=int, default=DEFAULT_COMMITTEE_SAMPLE)
    parser.add_argument("--json", action="store_true", help="emit the summary as compact JSON")
    return parser


async def _run(args: argparse.Namespace) -> dict[str, Any]:
    biennium = args.biennium or biennium_for_date(datetime.now(UTC).date())
    prior = args.prior_biennium or previous_biennium(biennium)
    sponsor_client = WSLClient("SponsorService")
    committee_client = WSLClient("CommitteeService")
    return await probe_member_identity(
        sponsor_client,
        committee_client,
        biennium=biennium,
        prior_biennium=prior,
        committee_sample=args.committee_sample,
    )


def main(argv: list[str] | None = None) -> int:
    configure_logging()
    args = _build_parser().parse_args(argv)
    summary = asyncio.run(_run(args))
    print(json.dumps(summary, indent=None if args.json else 2, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
