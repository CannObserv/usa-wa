"""Merged-span tenure builder (#78, epic #76) — the Phase B core.

A **pure** function that collapses per-member biennium observations into merged tenure
**spans** — the assignment analog of the committee rename-chain builder. Instead of one
Assignment per member-seat-*biennium* (the pre-#78 shape), a contiguous run of biennia
holding the same seat / party / committee becomes **one** span with a real
`valid_from..valid_to`. A 12-year senator is one span, not six.

The builder is generic over the tenure *kind* + *discriminator* (the callers — the WSL
sponsor Phase B, PDC #79, committee membership #82 — build the observations with the right
discriminator; e.g. party slug, Senate LD, House `LD:Position`, committee id). It knows
only biennium arithmetic:

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
from dataclasses import dataclass
from datetime import date

from usa_wa_adapter_legislature.synthesis import parse_biennium


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
    observations: list[Observation], *, current_biennium: str
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
