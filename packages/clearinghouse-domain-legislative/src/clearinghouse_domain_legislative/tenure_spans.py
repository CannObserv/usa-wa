"""Merged-span tenure builder (#78, epic #76) — the Phase B core.

A **pure** function that collapses per-member biennium observations into merged tenure
**spans** — the assignment analog of the committee rename-chain builder. Instead of one
Assignment per member-seat-*biennium* (the pre-#78 shape), a contiguous run of biennia
holding the same seat / party / committee becomes **one** span with a real
`valid_from..valid_to`. A 12-year senator is one span, not six.

The builder is generic over the tenure *kind* + *discriminator* (the callers — the WSL
sponsor Phase B, PDC #79, committee membership #82 — build the observations with the right
discriminator; e.g. party slug, Senate LD, House `LD:Position`, committee id). **The
discriminator choice is the caller's semantic decision**, and a changed discriminator
opens a new span: e.g. keying a Senate seat on its LD means a district *renumbered under
redistricting* (LD5→LD6) splits a continuously-serving senator into two spans. Whether
that's desired is for the emission increment (#78-2) to decide deliberately, not the
builder. It knows only biennium arithmetic:

- **Consecutive** biennia (each 2 years after the previous) merge into one span; a **gap**
  breaks it into two (dormancy = a genuine tenure break — the opposite of the committee
  "absence ≠ retirement" rule, because here we model a *served-this-biennium* fact, not
  entity existence).
- A span whose last biennium is the **current** one is the **open end** of an ongoing
  tenure: `valid_to=None`, `is_active=True`. Otherwise it's closed at Dec 31 of its last
  biennium's even year.
- The `source_id` keys on the tenure **start** biennium, so an extending span keeps its id
  (idempotent upsert updates `valid_to`) while a post-gap tenure opens a new-start span.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass, replace
from datetime import date

from clearinghouse_domain_legislative.span_kinds import KIND_PARTY
from clearinghouse_domain_legislative.terms import parse_biennium


@dataclass(frozen=True)
class Observation:
    """One member holding one tenure (``kind`` + ``discriminator``) in one biennium — the
    builder's input unit, emitted by a caller from an archived roster."""

    member_id: str
    kind: str
    discriminator: str
    biennium: str


@dataclass(frozen=True)
class TenureSpan:
    """A merged, contiguous tenure with resolved validity bounds. ``valid_to`` is ``None``
    (and ``is_active`` True) when the span reaches the current biennium."""

    member_id: str
    kind: str
    discriminator: str
    start_biennium: str
    end_biennium: str
    valid_from: date
    valid_to: date | None
    is_active: bool

    @property
    def source_id(self) -> str:
        """Deterministic Assignment ``source_id`` — keyed on the tenure start so re-runs are
        idempotent (an extending span keeps its id; a post-gap tenure gets a new one)."""
        return f"{self.member_id}:{self.kind}:{self.discriminator}:{self.start_biennium}"


def _consecutive_runs(ordered: list[str]) -> list[list[str]]:
    """Split biennia (ordered oldest→newest) into maximal runs of adjacent biennia (each 2
    years after the previous); a larger gap starts a new run."""
    runs: list[list[str]] = []
    current: list[str] = []
    for biennium in ordered:
        if current and parse_biennium(biennium)[0] != parse_biennium(current[-1])[0] + 2:
            runs.append(current)
            current = []
        current.append(biennium)
    if current:
        runs.append(current)
    return runs


def build_tenure_spans(
    observations: Iterable[Observation], *, current_biennium: str
) -> list[TenureSpan]:
    """Collapse ``observations`` into merged :class:`TenureSpan`s (deterministically ordered).

    Groups by ``(member_id, kind, discriminator)``, orders each group's biennia, splits on
    dormancy gaps, and resolves each run's validity window — the run reaching
    ``current_biennium`` stays open (``is_active``)."""
    grouped: dict[tuple[str, str, str], set[str]] = defaultdict(set)
    for obs in observations:
        grouped[(obs.member_id, obs.kind, obs.discriminator)].add(obs.biennium)

    spans: list[TenureSpan] = []
    for (member_id, kind, discriminator), biennia in grouped.items():
        ordered = sorted(biennia, key=lambda b: parse_biennium(b)[0])
        for run in _consecutive_runs(ordered):
            start, end = run[0], run[-1]
            reaches_current = end == current_biennium
            spans.append(
                TenureSpan(
                    member_id=member_id,
                    kind=kind,
                    discriminator=discriminator,
                    start_biennium=start,
                    end_biennium=end,
                    valid_from=date(parse_biennium(start)[0], 1, 1),
                    valid_to=None if reaches_current else date(parse_biennium(end)[1], 12, 31),
                    is_active=reaches_current,
                )
            )
    spans.sort(key=lambda s: (s.member_id, s.kind, s.discriminator, s.start_biennium))
    return spans


def merge_party_continuity(spans: list[TenureSpan]) -> list[TenureSpan]:
    """Collapse each member's same-party spans into one, whatever the gap (#289).

    Party membership is derived alongside seat tenure, so it inherits the seat's
    boundaries: a member who moves between seats, or leaves and returns, gets
    their party span closed with the old seat and reopened with the new one. We
    then assert two facts where there is one. Mike Chapman was a Democrat across
    the 27 days between his House seat and his Senate seat; Margaret Hurley was
    a Democrat across the 400 days between them; Elmer Huntley was a Republican
    across 759.

    **There is no upper bound on the gap, deliberately.** A break in elected
    service is not evidence about party membership — only an attested change of
    affiliation is, and that is exactly what the guard below preserves: two
    spans of one party do NOT merge across a span of a different one, because
    the member is attested under the other party in between and merging would
    both erase the switch and assert two affiliations at once.

    This is the one place the dormancy rule does not apply. Everywhere else it
    is right — ``build_tenure_spans`` splits a seat tenure on a biennium gap
    because a seat someone stopped holding is a tenure that ended, and we do not
    observe them while they are gone. Party is different in kind: it is an
    attribute of the person, not an office they occupy, so the thing the gap is
    evidence OF (they stopped holding that seat) says nothing about it.

    Keyed on the EARLIEST span's start, so the surviving ``source_id`` is the
    one already published and the later tenures are what retract — the merge
    costs a consumer nothing on the row it keeps.
    """
    by_member: dict[str, list[TenureSpan]] = defaultdict(list)
    passthrough: list[TenureSpan] = []
    for span in spans:
        if span.kind == KIND_PARTY:
            by_member[span.member_id].append(span)
        else:
            passthrough.append(span)

    merged: list[TenureSpan] = []
    for member_spans in by_member.values():
        ordered = sorted(member_spans, key=lambda s: (s.valid_from, s.start_biennium))
        run: TenureSpan | None = None
        for span in ordered:
            if run is None:
                run = span
                continue
            if span.discriminator == run.discriminator:
                run = _extend(run, span)
                continue
            # A different affiliation: close the run and start a new one. Any
            # later span of the first party opens its own run rather than
            # rejoining this one — the switch is the documented break.
            merged.append(run)
            run = span
        if run is not None:
            merged.append(run)

    return sorted(
        [*passthrough, *merged],
        key=lambda s: (s.member_id, s.kind, s.discriminator, s.start_biennium),
    )


def _extend(run: TenureSpan, later: TenureSpan) -> TenureSpan:
    """``run`` widened to cover ``later`` — the merged window is their **union**.

    So the end is the MAXIMUM of the two, never the tail's: spans arrive ordered
    by ``valid_from``, which says nothing about where they end, and a later span
    nested inside the run would otherwise discard the years the run already
    covered past it.

    Openness is part of that same maximum — an open span has no end at all — so
    ``is_active`` survives from either side and ``valid_to`` derives from it.
    That is the sitting-legislator case: a member still serving keeps an open
    party span whichever half of the run is the open one.

    ``valid_from`` and ``start_biennium`` are the run's, untouched by ``replace``
    — that is what keys the merged span on the earliest start.
    """
    is_active = run.is_active or later.is_active
    return replace(
        run,
        end_biennium=max(run.end_biennium, later.end_biennium, key=lambda b: parse_biennium(b)[0]),
        valid_to=None
        if is_active
        else max(filter(None, (run.valid_to, later.valid_to)), default=None),
        is_active=is_active,
    )
