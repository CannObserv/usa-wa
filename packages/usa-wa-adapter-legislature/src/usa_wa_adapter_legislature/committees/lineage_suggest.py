"""C5 committee lineage-candidate curation assist (usa-wa#124) — advisory only.

The operator attests succession links (C2) by judgment; this **suggests** which era-``Id``
pairs to look at, never asserts one. It is the "fully automatic" grouping repurposed as a
hint tool (the hybrid boundary): ground-truth stays with the operator, who runs
``committees.succession_cli`` to record the link.

Signals combined per candidate pair (same chamber only):

- **Name similarity** — Jaccard over *significant* name tokens (stopwords like
  ``committee``/``senate``/``on`` dropped), so "Labor, Commerce & Consumer Protection" and
  "Labor and Commerce" score high.
- **Adjacent windows** — the predecessor's ``dissolved`` year sits at/just before the
  successor's ``founded`` year (a clean hand-off), from the C1a windows.
- **Membership carry-over** — Persons who served on both committees (a re-org keeps
  people), from the local membership Assignments.

The composite score ranks the report; nothing is written.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import re
import sys
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from clearinghouse_core.logging import configure_logging
from clearinghouse_core.provenance import Source
from clearinghouse_domain_legislative.identity import Assignment, Organization, Role
from clearinghouse_domain_legislative.terms import biennium_for_date
from usa_wa_adapter_legislature.committees.cohort import CommitteeRosterCohortProvider
from usa_wa_adapter_legislature.committees.lifecycle import (
    CommitteeWindow,
    collect_committee_presence,
    derive_committee_windows,
)
from usa_wa_adapter_legislature.transport import WSLClient

_SOURCE = "usa_wa_legislature"
_ORG_TYPE = "committee"

#: Dropped before token comparison — chamber/structural words carry no lineage signal.
_STOPWORDS = frozenset(
    {
        "committee",
        "on",
        "the",
        "and",
        "of",
        "for",
        "to",
        "a",
        "an",
        "senate",
        "house",
        "joint",
        "select",
        "special",
        "standing",
        "washington",
        "state",
        "legislature",
    }
)

_MIN_NAME_SIMILARITY = 0.34


def significant_tokens(name: str) -> frozenset[str]:
    """Folded, stopword-stripped name tokens (intra-word hyphens split)."""
    tokens = re.split(r"[^a-z0-9]+", name.lower())
    return frozenset(t for t in tokens if t and t not in _STOPWORDS)


def name_similarity(a: frozenset[str], b: frozenset[str]) -> float:
    """Jaccard overlap of two token sets (0.0 when either is empty)."""
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


@dataclass(frozen=True)
class CandidateInfo:
    """One committee org's lineage-relevant features."""

    source_id: str
    name: str
    chamber_key: str | None
    window: CommitteeWindow | None
    member_ids: frozenset[str] = frozenset()

    @property
    def tokens(self) -> frozenset[str]:
        return significant_tokens(self.name)


@dataclass
class SuccessionCandidate:
    """A suggested predecessor→successor pair (or direction-uncertain)."""

    predecessor_id: str
    successor_id: str
    score: float
    direction_certain: bool
    reasons: list[str] = field(default_factory=list)


def _window_order(a: CandidateInfo, b: CandidateInfo) -> tuple[CandidateInfo, CandidateInfo] | None:
    """Order (pred, succ) by windows: the one that dissolved feeds the later/current one.

    Returns None when the windows don't imply a direction (both current, or neither dated)."""
    aw, bw = a.window, b.window
    a_dissolved = aw.dissolved_year if aw else None
    b_dissolved = bw.dissolved_year if bw else None
    a_current = bool(aw and aw.is_current)
    b_current = bool(bw and bw.is_current)
    if a_dissolved is not None and (b_current or b_dissolved is None or b_dissolved > a_dissolved):
        return a, b
    if b_dissolved is not None and (a_current or a_dissolved is None or a_dissolved > b_dissolved):
        return b, a
    return None


def _adjacent(pred: CandidateInfo, succ: CandidateInfo) -> bool:
    """The predecessor dissolved at/just before the successor was founded (≤2y gap)."""
    if pred.window is None or succ.window is None:
        return False
    d, f = pred.window.dissolved_year, succ.window.founded_year
    return d is not None and f is not None and 0 <= f - d <= 2


def suggest_candidates(
    infos: Sequence[CandidateInfo], *, min_name_similarity: float = _MIN_NAME_SIMILARITY
) -> list[SuccessionCandidate]:
    """Rank suggested succession pairs (same chamber, name-similar). Advisory, pure."""
    candidates: list[SuccessionCandidate] = []
    for i in range(len(infos)):
        for j in range(i + 1, len(infos)):
            a, b = infos[i], infos[j]
            if a.chamber_key is None or a.chamber_key != b.chamber_key:
                continue
            sim = name_similarity(a.tokens, b.tokens)
            if sim < min_name_similarity:
                continue
            ordered = _window_order(a, b)
            direction_certain = ordered is not None
            pred, succ = ordered if ordered is not None else (a, b)
            reasons = [f"name_similarity={sim:.2f}"]
            score = sim
            if _adjacent(pred, succ):
                reasons.append("adjacent_windows")
                score += 0.5
            shared = pred.member_ids & succ.member_ids
            if shared:
                reasons.append(f"shared_members={len(shared)}")
                score += min(0.5, 0.05 * len(shared))
            if not direction_certain:
                reasons.append("direction_uncertain")
            candidates.append(
                SuccessionCandidate(
                    predecessor_id=pred.source_id,
                    successor_id=succ.source_id,
                    score=round(score, 3),
                    direction_certain=direction_certain,
                    reasons=reasons,
                )
            )
    candidates.sort(key=lambda c: c.score, reverse=True)
    return candidates


async def build_candidate_infos(
    session: AsyncSession, windows: dict[str, CommitteeWindow]
) -> list[CandidateInfo]:
    """Assemble a :class:`CandidateInfo` per produced committee org (name, chamber, members)."""
    orgs = (
        (
            await session.execute(
                select(Organization).where(
                    Organization.source == _SOURCE, Organization.org_type == _ORG_TYPE
                )
            )
        )
        .scalars()
        .all()
    )
    infos: list[CandidateInfo] = []
    for org in orgs:
        member_rows = (
            (
                await session.execute(
                    select(Assignment.person_id)
                    .join(Role, Assignment.role_id == Role.id)
                    .where(Role.organization_id == org.id, Assignment.person_id.is_not(None))
                    .distinct()
                )
            )
            .scalars()
            .all()
        )
        infos.append(
            CandidateInfo(
                source_id=org.source_id,
                name=org.name,
                chamber_key=str(org.parent_organization_id) if org.parent_organization_id else None,
                window=windows.get(org.source_id),
                member_ids=frozenset(str(m) for m in member_rows),
            )
        )
    return infos


async def _run(biennium: str, *, min_name_similarity: float) -> list[SuccessionCandidate]:
    engine = create_async_engine(os.environ["DATABASE_URL"])
    wsl_client = WSLClient("CommitteeService")
    try:
        async with AsyncSession(engine) as session:
            source = (
                await session.execute(select(Source).where(Source.slug == _SOURCE))
            ).scalar_one_or_none()
            provider = CommitteeRosterCohortProvider(
                wsl_client, session=session, source_id=(source.id if source else None)
            )
            presence = await collect_committee_presence(provider)
            archived = await provider.archived_bienniums()
            windows = derive_committee_windows(
                presence, current_biennium=biennium, archived_bienniums=archived
            )
            infos = await build_candidate_infos(session, windows)
    finally:
        await engine.dispose()
    return suggest_candidates(infos, min_name_similarity=min_name_similarity)


def _format(c: SuccessionCandidate) -> str:
    arrow = "->" if c.direction_certain else "~"
    return f"{c.score:>6.3f}  {c.predecessor_id} {arrow} {c.successor_id}  [{', '.join(c.reasons)}]"


def main(argv: list[str] | None = None) -> int:
    configure_logging()
    parser = argparse.ArgumentParser(
        description="Suggest committee succession candidates (advisory, read-only; usa-wa#124 C5)."
    )
    parser.add_argument(
        "--biennium", default=None, help="Biennium label; default USA_WA_BIENNIUM/date."
    )
    parser.add_argument("--min-name-similarity", type=float, default=_MIN_NAME_SIMILARITY)
    args = parser.parse_args(argv)
    if not os.environ.get("DATABASE_URL"):
        print("DATABASE_URL is not set; aborting", file=sys.stderr)
        return 2
    biennium = (
        args.biennium
        or os.environ.get("USA_WA_BIENNIUM")
        or biennium_for_date(datetime.now(UTC).date())
    )
    candidates = asyncio.run(_run(biennium, min_name_similarity=args.min_name_similarity))
    for c in candidates:
        print(_format(c))
    print(f"{len(candidates)} candidate pair(s) — advisory; attest via committees.succession_cli")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
