"""The pre-1991 roster build's acceptance oracle (#233, #267).

Pure checks the build runs before any write: the identity partition is exact, no member
sits on two Senate seats in one session year, and no overlay-synthesized span is emitted
as if the edition attested it. The #302 pipeline runs the same checks on the same inputs,
so they left :mod:`usa_wa_adapter_legislature.roster_pdf.build`, the Postgres write path
#412 PR F deletes.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Sequence

from clearinghouse_domain_legislative.tenure_spans import TenureSpan
from usa_wa_adapter_legislature.roster_pdf.identity import RosterIdentity
from usa_wa_adapter_legislature.roster_pdf.normalize import RosterRecord


class OracleViolation(RuntimeError):
    """A hard acceptance-oracle failure — the build must not write."""


def verify_pre1991(
    identities: Sequence[RosterIdentity],
    pre_records: Iterable[RosterRecord],
    *,
    refused_records: int = 0,
) -> None:
    """The hard half of the acceptance oracle. Raises :class:`OracleViolation`.

    * **Partition exactness** (item 1): identities plus refusals account for every pre-1991
      record — zero silent drops, zero double counting.
    * **Person-side Senate simultaneity** (item 3): no member covers two Senate seats in one
      *session year*. The seat side is the projector's reported overlaps (same-biennium
      handoffs are legitimate); the person side is corrupt data nothing downstream checks —
      a member listed under two LDs across a redistricting boundary would trip it.

    Refusals, declines and uncovered rows are *tallied* outcomes the summary reports; they
    never abort.
    """
    total = sum(1 for _ in pre_records)
    placed = sum(len(i.records) for i in identities) + refused_records
    if placed != total:
        raise OracleViolation(
            f"partition mismatch: {total} pre-1991 records, {placed} placed "
            "(identities + refusals) — a record was dropped or double-counted"
        )
    for identity in identities:
        member = identity.wsl_member_id or identity.key or identity.fold
        seats_by_year: dict[int, set[int]] = defaultdict(set)
        for record in identity.records:
            if record.chamber == "senate":
                seats_by_year[record.year].add(record.district)
        doubled = {year: lds for year, lds in seats_by_year.items() if len(lds) > 1}
        if doubled:
            raise OracleViolation(
                f"person-side Senate simultaneity: {member} ({identity.fold}) listed on "
                f"multiple Senate seats in one session year: {sorted(doubled.items())}"
            )


def unattested_spans(minted: Sequence[TenureSpan], built: Sequence[TenureSpan]) -> list[TenureSpan]:
    """The overlay-added spans this builder must not emit — a seat the wire built nothing for.

    The roster emitter has no ``skip_citation_ids``, so every span it emits cites the archived
    edition. That is right for anything the edition listed and wrong for a **synthesized** span
    (a current-biennium ``seated`` the wire missed, or a #105-excluded mover's closed House
    tenure), which would then cite an edition that never named the member in that seat.

    The #267 **split** also adds a span, and that one is citable: the tail is the same member's
    same tenure after a gap, and the edition lists them in exactly those biennia. What separates
    the two is the **seat**, not the count — a split tail always shares
    ``(member, kind, discriminator)`` with a built span, a synthesized one never does. The first
    version of this guard compared whole ``source_id`` sets, could not tell them apart, and
    aborted the production build on the first real split.
    """
    built_seats = {(s.member_id, s.kind, s.discriminator) for s in built}
    return [s for s in minted if (s.member_id, s.kind, s.discriminator) not in built_seats]
