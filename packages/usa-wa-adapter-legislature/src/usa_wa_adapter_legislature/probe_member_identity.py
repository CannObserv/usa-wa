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

**Deep-history mode (#81).** `--history` sweeps **every consecutive biennium pair** from
the WSL floor (`1991-92`) to current, tallying same-name/different-``Id`` divergences —
the pre-flight for the historical member backfill (#77), which mints ~800 Persons keyed
on ``Id``. Divergences are classified: a **re-key** keeps the seat (same District, new
``Id``) and forks one person (alarming); a **name collision** (different District) is two
distinct people sharing a name, which the ``Id`` correctly separates (benign — doesn't
count against stability). **Finding (2026-07-08): ``Id`` is stable across all 17 boundaries
1991-92→2025-26 — 0 re-keys.** The one divergence is a benign collision (two "Brian
Sullivan"s, LD29 vs LD21), which *validates* keying on ``Id`` over name. Corollary for
#77: dedup Persons by ``Id``, never by name — two Persons may legitimately share a name.

    python -m usa_wa_adapter_legislature.probe_member_identity
    python -m usa_wa_adapter_legislature.probe_member_identity --biennium 2025-26 --json
    python -m usa_wa_adapter_legislature.probe_member_identity --history        # deep sweep (#81)
"""

import argparse
import asyncio
import json
import sys
from datetime import UTC, datetime
from typing import Any

from zeep.exceptions import Fault

from clearinghouse_core.logging import configure_logging, get_logger
from usa_wa_adapter_legislature.normalize.members import is_person
from usa_wa_adapter_legislature.refresh import biennium_for_date, previous_biennium
from usa_wa_adapter_legislature.transport import WSLClient, _is_biennium_out_of_range

logger = get_logger(__name__)

#: The WSL ``GetSponsors`` history floor (probed 2026-07-08 — 1989-90 faults). The deep
#: sweep (#81) walks back to here to confirm ``Id`` stability before the historical mint.
DEFAULT_HISTORY_FLOOR = "1991-92"

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


def _same_district(divergence: dict[str, Any]) -> bool:
    """Whether a same-name/different-Id pair kept the same District — a genuine seat re-key
    (alarming) vs. a name collision between two people in different districts (benign).

    Both districts blank/``None`` compare equal → treated as a re-key: when the seat is
    unknown we can't rule out a genuine re-key, so we bias to the alarming bucket (flag for
    review) rather than silently discounting it as a collision."""
    return str(divergence.get("district_a")) == str(divergence.get("district_b"))


def biennium_chain(from_biennium: str, to_biennium: str, *, max_len: int = 200) -> list[str]:
    """The ordered biennium labels from ``from_biennium`` to ``to_biennium`` (oldest first).

    Built by walking :func:`previous_biennium` back from ``to_biennium`` until
    ``from_biennium`` is hit — so ``from_biennium`` must be older than (or equal to)
    ``to_biennium`` and on the chain, else ``ValueError`` (guards a reversed/typo range)."""
    chain = [to_biennium]
    for _ in range(max_len):
        if chain[-1] == from_biennium:
            return list(reversed(chain))
        chain.append(previous_biennium(chain[-1]))
    raise ValueError(f"{from_biennium!r} not reachable walking back from {to_biennium!r}")


async def sweep_id_stability_history(
    sponsor_client: Any,
    *,
    from_biennium: str = DEFAULT_HISTORY_FLOOR,
    to_biennium: str,
) -> dict[str, Any]:
    """Deep-history ``Id``-stability sweep (#81): compare **every consecutive biennium
    pair** from ``from_biennium`` to ``to_biennium`` and tally same-name/different-``Id``
    divergences — the re-key signal, checked at depth before minting ~800 historical
    Persons keyed on the WSL ``Id``.

    Each biennium is pulled **once** (cached; it appears in two adjacent pairs). A biennium
    that faults (below the WSL floor) or returns no persons is **absent** — the boundary
    is skipped, not scored (no evidence, not a divergence). ``id_is_stable_across_history``
    is True only when at least one boundary had data on both sides and **no** boundary
    diverged."""
    chain = biennium_chain(from_biennium, to_biennium)
    cache: dict[str, list[dict[str, Any]] | None] = {}

    async def persons(b: str) -> list[dict[str, Any]] | None:
        if b not in cache:
            try:
                rows = await sponsor_client.get_sponsors(b)
                cache[b] = [m for m in rows if is_person(m)]
            except Fault as exc:
                if not _is_biennium_out_of_range(exc):
                    raise  # a real/transient WSL fault must not read as an empty floor
                cache[b] = None  # below the floor / invalid biennium
        return cache[b]

    boundaries: list[dict[str, Any]] = []
    divergences: list[dict[str, Any]] = []
    compared = 0
    for older, newer in zip(chain, chain[1:], strict=False):
        a, b = await persons(older), await persons(newer)
        label = f"{older}->{newer}"
        if not a or not b:  # None (fault) or empty → no evidence at this boundary
            boundaries.append({"boundary": label, "absent": True})
            continue
        cmp = compare_id_stability(a, b)
        compared += 1
        boundaries.append(
            {"boundary": label, **{k: cmp[k] for k in ("matched", "same_id", "diff_id", "stable")}}
        )
        divergences.extend({"boundary": label, **d} for d in cmp["divergences"])

    # Classify each same-name/different-Id pair. A genuine **re-key** keeps the seat
    # (same District, new Id) — the alarming signal that would fork one person. A pair with
    # a **different District** is far more likely two distinct people who share a name (the
    # name-only key can't tell them apart, but the Id correctly does) — a benign collision,
    # NOT evidence the Id is unstable. Only re-keys count against stability.
    rekeys = [d for d in divergences if _same_district(d)]
    collisions = [d for d in divergences if not _same_district(d)]
    with_data = [b for b in chain if cache.get(b)]
    stable = compared > 0 and not rekeys
    logger.info(
        "sweep_id_stability_history",
        extra={
            "from_biennium": from_biennium,
            "to_biennium": to_biennium,
            "boundaries_compared": compared,
            "rekeys": len(rekeys),
            "name_collisions": len(collisions),
            "id_is_stable_across_history": stable,
        },
    )
    return {
        "from_biennium": from_biennium,
        "to_biennium": to_biennium,
        "boundaries_compared": compared,
        "deepest_with_data": with_data[0] if with_data else None,
        "id_is_stable_across_history": stable,
        "total_divergences": len(divergences),
        "rekeys": rekeys,
        "name_collisions": collisions,
        "boundaries": boundaries,
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
    parser.add_argument(
        "--history",
        action="store_true",
        help="deep sweep: check Id stability across every consecutive biennium pair (#81)",
    )
    parser.add_argument(
        "--from-biennium",
        default=DEFAULT_HISTORY_FLOOR,
        help=f"--history floor (default {DEFAULT_HISTORY_FLOOR}, the WSL GetSponsors floor)",
    )
    parser.add_argument("--json", action="store_true", help="emit the summary as compact JSON")
    return parser


async def _run(args: argparse.Namespace) -> dict[str, Any]:
    biennium = args.biennium or biennium_for_date(datetime.now(UTC).date())
    if args.history:
        return await sweep_id_stability_history(
            WSLClient("SponsorService"),
            from_biennium=args.from_biennium,
            to_biennium=biennium,
        )
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
