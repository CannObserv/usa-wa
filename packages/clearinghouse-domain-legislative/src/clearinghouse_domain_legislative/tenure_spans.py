"""Merged-span tenure builder (#78, epic #76) — the span engine's core.

A **pure** function that collapses per-member biennium observations into merged tenure
**spans** — the assignment analog of the committee rename-chain builder. Instead of one
Assignment per member-seat-*biennium* (the pre-#78 shape), a contiguous run of biennia
holding the same seat / party / committee becomes **one** span with a real
`valid_from..valid_to`. A 12-year senator is one span, not six.

The builder is generic over the tenure *kind* + *discriminator* (the callers — the
conformed sponsor, committee-membership #82 and House builders — build the observations with
the right discriminator; e.g. party slug, Senate LD, House `LD:Position`, committee id). **The
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
- An observation flagged ``opens_tenure`` **breaks** a run even with no gap (#517): a
  member who left and came back inside adjacent biennia has none to split on, so the
  source that can see the return says so, and the return is a new-start span too.

**One kind is exempt from the dormancy rule, and the module knows its name** (#289):
`merge_party_continuity` rejoins a member's same-party spans across ANY gap. This is the
single place the generic-over-kind claim above is qualified, and it is qualified
deliberately rather than pushed to a caller — the dormancy split and its one exemption are
the same decision, and splitting them across layers is how the two tiers would come to
disagree about it. The rule: a seat someone stopped holding is a tenure that ended, but a
party affiliation is an attribute of the *person*, not an office they occupy, so a break in
elected service is no evidence about it. Only an attested span of a **different** party in
between breaks a run — a documented switch. `KIND_PARTY` is the sole span-kind constant
this module imports; any other kind-specific rule belongs to its caller.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Collection, Iterable
from dataclasses import dataclass, replace
from datetime import date

from clearinghouse_domain_legislative.boundary_basis import (
    BASIS_DATED,
    BASIS_OBSERVED,
    BASIS_OPEN,
    BOUNDARY_BASES,
    START_BASES,
)
from clearinghouse_domain_legislative.membership_types import (
    MEMBERSHIP_PRINCIPAL,
    MEMBERSHIP_TYPES,
)
from clearinghouse_domain_legislative.span_kinds import KIND_PARTY
from clearinghouse_domain_legislative.terms import biennium_start_date, parse_biennium


@dataclass(frozen=True)
class Observation:
    """One member holding one tenure (``kind`` + ``discriminator``) in one biennium — the
    builder's input unit, emitted by a caller from an archived roster."""

    member_id: str
    kind: str
    discriminator: str
    biennium: str
    #: This biennium opens a NEW tenure even if the previous one was held too (#517): a
    #: return to the seat after someone else held it, inside contiguous biennia. Only a
    #: source that can see the interlude knows; the builder cannot infer it.
    opens_tenure: bool = False


@dataclass(frozen=True)
class TenureSpan:
    """A merged, contiguous tenure with resolved validity bounds. ``valid_to`` is ``None``
    (and ``is_active`` True) when the span reaches the current biennium.

    **Each edge carries its basis** (usa-wa#501 D6, :mod:`.boundary_basis`): the writer
    that puts an edge somewhere says where its date came from. The defaults are the
    builder's own — an observed start, and an end that is observed or, with no
    ``valid_to``, open — so a writer that MOVES an edge must restate its basis; a
    ``replace`` that opens a closed span without doing so is refused here, at the
    write. ``membership_type`` (#501 D5) says how the tenure holds its role; only a
    principal counts toward a seat's capacity.
    """

    member_id: str
    kind: str
    discriminator: str
    start_biennium: str
    end_biennium: str
    valid_from: date
    valid_to: date | None
    is_active: bool
    start_basis: str = BASIS_OBSERVED
    #: ``None`` only as a constructor default: resolved to ``open`` or ``observed``.
    end_basis: str | None = None
    membership_type: str = MEMBERSHIP_PRINCIPAL
    #: A dated start known only to a window (#501 D6a: a month, a year, or an election
    #: opening it) — the window's far edge, which ``valid_from_latest`` reports. ``None``
    #: for a start dated to the day, and always for any other basis.
    start_window_end: date | None = None
    #: The mirror for a dated end known only to a window: its near edge.
    end_window_start: date | None = None

    def __post_init__(self) -> None:
        if self.end_basis is None:
            default = BASIS_OPEN if self.valid_to is None else BASIS_OBSERVED
            object.__setattr__(self, "end_basis", default)
        if self.start_basis not in START_BASES:
            raise ValueError(f"start_basis {self.start_basis!r} is not one of {START_BASES}")
        if self.end_basis not in BOUNDARY_BASES:
            raise ValueError(f"end_basis {self.end_basis!r} is not one of {BOUNDARY_BASES}")
        if (self.end_basis == BASIS_OPEN) != (self.valid_to is None):
            raise ValueError(
                f"end_basis {self.end_basis!r} with valid_to {self.valid_to}: an end is "
                "open exactly when valid_to is null"
            )
        if self.membership_type not in MEMBERSHIP_TYPES:
            raise ValueError(
                f"membership_type {self.membership_type!r} is not one of {MEMBERSHIP_TYPES}"
            )
        self._check_windows()

    def _check_windows(self) -> None:
        """A window sits on a DATED edge, inside the span — so a writer that moves a
        windowed edge must restate it, or the write fails here."""
        if self.start_window_end is not None:
            if self.start_basis != BASIS_DATED:
                raise ValueError(f"a start window needs a dated start, not {self.start_basis!r}")
            end = self.valid_to or date.max
            if not self.valid_from <= self.start_window_end <= end:
                raise ValueError(
                    f"start window end {self.start_window_end} lies outside "
                    f"[{self.valid_from}, {self.valid_to}]"
                )
        if self.end_window_start is not None:
            if self.end_basis != BASIS_DATED or self.valid_to is None:
                raise ValueError(f"an end window needs a dated end, not {self.end_basis!r}")
            if not self.valid_from <= self.end_window_start <= self.valid_to:
                raise ValueError(
                    f"end window start {self.end_window_start} lies outside "
                    f"[{self.valid_from}, {self.valid_to}]"
                )

    @property
    def valid_from_latest(self) -> date:
        """The latest the true start can be: ``[valid_from, valid_from_latest]`` holds it.

        Exact for a dated day. An observed start was first attested somewhere in its
        start biennium, and a derived one (moved onto another tenure's boundary) is no
        earlier than the moved date; either way the bound is that biennium's last day,
        clamped by the span's own end (#501 D6)."""
        if self.start_basis == BASIS_DATED:
            return self.start_window_end or self.valid_from
        ceiling = biennium_end_date(self.start_biennium)
        bound = ceiling if self.valid_to is None else min(ceiling, self.valid_to)
        return max(self.valid_from, bound)

    @property
    def valid_to_earliest(self) -> date | None:
        """The earliest the true end can be: ``[valid_to_earliest, valid_to]`` holds it;
        ``None`` while the tenure is open.

        The mirror of :attr:`valid_from_latest`: exact for a dated day, else the end
        biennium's first day, clamped by the span's own start — and, for a derived end,
        never later than the moved date itself."""
        if self.valid_to is None:
            return None
        if self.end_basis == BASIS_DATED:
            return self.end_window_start or self.valid_to
        floor = max(biennium_start_date(self.end_biennium), self.valid_from)
        return min(self.valid_to, floor)

    @property
    def source_id(self) -> str:
        """Deterministic Assignment ``source_id`` — keyed on the tenure start so re-runs are
        idempotent (an extending span keeps its id; a post-gap tenure gets a new one)."""
        return f"{self.member_id}:{self.kind}:{self.discriminator}:{self.start_biennium}"


def biennium_end_date(label: str) -> date:
    """December 31 of ``label``'s second year — the ceiling a closed span sits on."""
    return date(parse_biennium(label)[1], 12, 31)


def _consecutive_runs(ordered: list[str], breaks: Collection[str] = ()) -> list[list[str]]:
    """Split biennia (ordered oldest→newest) into maximal runs of adjacent biennia (each 2
    years after the previous); a larger gap, or a biennium in ``breaks``, starts a new run."""
    runs: list[list[str]] = []
    current: list[str] = []
    for biennium in ordered:
        gap = current and parse_biennium(biennium)[0] != parse_biennium(current[-1])[0] + 2
        if current and (gap or biennium in breaks):
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
    dormancy gaps and ``opens_tenure`` breaks, and resolves each run's validity window — the
    run reaching ``current_biennium`` stays open (``is_active``)."""
    grouped: dict[tuple[str, str, str], set[str]] = defaultdict(set)
    breaks: dict[tuple[str, str, str], set[str]] = defaultdict(set)
    for obs in observations:
        key = (obs.member_id, obs.kind, obs.discriminator)
        grouped[key].add(obs.biennium)
        if obs.opens_tenure:
            breaks[key].add(obs.biennium)

    spans: list[TenureSpan] = []
    for (member_id, kind, discriminator), biennia in grouped.items():
        ordered = sorted(biennia, key=lambda b: parse_biennium(b)[0])
        for run in _consecutive_runs(ordered, breaks[(member_id, kind, discriminator)]):
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
                    valid_to=None if reaches_current else biennium_end_date(end),
                    is_active=reaches_current,
                    start_basis=BASIS_OBSERVED,
                    end_basis=BASIS_OPEN if reaches_current else BASIS_OBSERVED,
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

    Keyed on the earliest-**dated** span's start — the run head is chosen by
    ``valid_from``, and the merged span takes that head's ``valid_from`` and
    ``start_biennium`` together. Usually the earliest-dated span is also the
    earliest-keyed one; a ``seated`` that back-dates a span below its own
    biennium floor (#272, Graham Hunt: keyed ``2015-16``, dated 2014-01-17)
    makes them different rows, and dated is the one to follow — it keeps the
    surviving key and the surviving start consistent, which is the pair
    ``misdated_tenures()`` checks. Either way the surviving ``source_id`` is one
    already published and the later tenures are what retract, so the merge costs
    a consumer nothing on the row it keeps.
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
        # Through `parse_biennium`, like every other biennium comparison in this module
        # (CR 5): a label is `YYYY-YY`, so string order happens to agree today, but the
        # tiebreak decides which span the run keys on — and therefore which `source_id`
        # survives the merge. Too load-bearing to rest on a label's spelling.
        ordered = sorted(
            member_spans, key=lambda s: (s.valid_from, parse_biennium(s.start_biennium)[0])
        )
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
    — taken together, which is what keys the merged span on its earliest-dated
    start without misdating it. So is ``start_basis``; the end — its date, its
    basis and its ``end_biennium`` — is whichever closed side ends last
    (usa-wa#501 D6, CR 1).
    """
    is_active = run.is_active or later.is_active
    latest_biennium = max(run.end_biennium, later.end_biennium, key=lambda b: parse_biennium(b)[0])
    if is_active:
        end_biennium, valid_to, end_basis = latest_biennium, None, BASIS_OPEN
    else:
        # One tenure's end, whole (usa-wa#501 CR 1): its date, its basis and the biennium it
        # was last attested in. A later-attested span closed early by an event must not lend
        # its biennium to the other span's observed ceiling.
        last = max((run, later), key=lambda s: (s.valid_to, parse_biennium(s.end_biennium)[0]))
        end_biennium, valid_to, end_basis = last.end_biennium, last.valid_to, last.end_basis
    return replace(
        run,
        end_biennium=end_biennium,
        valid_to=valid_to,
        is_active=is_active,
        end_basis=end_basis,
    )
