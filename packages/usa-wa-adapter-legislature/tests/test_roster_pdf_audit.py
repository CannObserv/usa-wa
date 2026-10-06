"""The roster's read-only audit oracle (#225).

Split from the Phase A harvest's tests when #412 PR F deleted `roster_pdf.harvest`: the
on-demand raw harvest (#421) archives the edition now, and the audit is pure.
"""

from __future__ import annotations

import pytest

from usa_wa_adapter_legislature.roster_pdf.audit import (
    RosterAudit,
    audit_roster,
    match_rate,
)
from usa_wa_adapter_legislature.roster_pdf.normalize import RosterRecord


def _record(**kw) -> RosterRecord:
    base = dict(
        district=39,
        chamber="house",
        year=1991,
        order=1,
        name="John Wynne",
        party_token="R",
        annotation=None,
        page_number=1,
    )
    base.update(kw)
    return RosterRecord(**base)


class TestMatchRate:
    def test_reports_rather_than_asserts(self) -> None:
        """#228 is gated on this number, so it must be reported even when poor."""
        rate = match_rate(matched=3, total=4)
        assert rate == pytest.approx(0.75)

    def test_empty_cohort_is_zero_not_a_division_error(self) -> None:
        assert match_rate(matched=0, total=0) == 0.0


class TestAuditOracle:
    """The acceptance oracle: the roster must independently reproduce the #144 findings with
    no hand-curation, from the source alone."""

    def test_flags_a_span_the_roster_does_not_attest(self) -> None:
        """Wynne: our record once claimed an LD39 *Senate* seat in 1991-92 that the roster
        shows only as LD39 House. A chamber-conflation artifact."""
        audit = audit_roster(
            records=[_record(district=39, chamber="house", year=1991, name="John Wynne")],
            claims=[("John Wynne", 39, "senate", 1991)],
        )
        assert isinstance(audit, RosterAudit)
        assert ("John Wynne", 39, "senate", 1991) in audit.unattested
        assert audit.attested == ()

    def test_confirms_a_span_the_roster_does_attest(self) -> None:
        """Braun: a genuine substitution, corroborated rather than flagged.

        Values are the roster's own, verbatim from the 2025-06-05 edition: a **five-day**
        appointment in LD20's Senate seat, which is both why #144 concluded it was genuine and a
        clean demonstration of the accuracy payload — a biennium-quantized span cannot express
        five days, and the source dates it exactly.
        """
        audit = audit_roster(
            records=[
                _record(
                    district=20,
                    chamber="senate",
                    year=2017,
                    name="Marlo Braun",
                    annotation=(
                        "Appointed to temporarily serve from July 18, 2017 until July 23, 2017"
                    ),
                )
            ],
            claims=[("Marlo Braun", 20, "senate", 2017)],
        )
        assert audit.unattested == ()
        assert ("Marlo Braun", 20, "senate", 2017) in audit.attested

    def test_matches_names_despite_source_formatting(self) -> None:
        """The roster writes honorifics, nicknames and initials the wires do not: a match must
        survive ``Robert "Bob" McCaslin`` vs ``Bob McCaslin``, or every pre-1991 span reads as
        unattested and the oracle is worthless."""
        audit = audit_roster(
            records=[_record(district=4, chamber="house", year=2017, name='Robert "Bob" McCaslin')],
            claims=[("Bob McCaslin", 4, "house", 2017)],
        )
        assert audit.unattested == ()

    def test_reports_the_match_rate(self) -> None:
        audit = audit_roster(
            records=[_record(name="John Wynne", district=39, chamber="house", year=1991)],
            claims=[
                ("John Wynne", 39, "house", 1991),
                ("Nobody Here", 12, "senate", 1991),
            ],
        )
        assert audit.match_rate == pytest.approx(0.5)


class TestTermCoverage:
    """The roster lists a member only in the year their term *begins*, so a claim must be
    matched against the term a row opens — not against the row's year alone."""

    def test_a_senator_is_attested_mid_term(self) -> None:
        """Adam Kline's roster rows are 1995/1999/2003; he sat through 1997 and 2001 too.
        Exact-year matching marked those unattested and buried the real artifacts."""
        records = [
            _record(district=37, chamber="senate", year=1995, name="Adam Kline"),
            _record(district=37, chamber="senate", year=1999, name="Adam Kline"),
        ]
        audit = audit_roster(
            records=records,
            claims=[("Adam Kline", 37, "senate", y) for y in (1995, 1997, 1999, 2001)],
        )
        assert audit.unattested == ()

    def test_a_senate_term_does_not_cover_the_next_one(self) -> None:
        """Four years, not forever — a lapsed senator must still fall out."""
        audit = audit_roster(
            records=[_record(district=37, chamber="senate", year=1995, name="Adam Kline")],
            claims=[("Adam Kline", 37, "senate", 1999)],
        )
        assert len(audit.unattested) == 1

    def test_a_house_term_covers_only_its_biennium(self) -> None:
        audit = audit_roster(
            records=[_record(district=2, chamber="house", year=2003, name="Roger Bush")],
            claims=[("Roger Bush", 2, "house", 2003), ("Roger Bush", 2, "house", 2005)],
        )
        assert audit.attested == (("Roger Bush", 2, "house", 2003),)
        assert audit.unattested == (("Roger Bush", 2, "house", 2005),)


class TestUnknownChamber:
    def test_an_unrecognised_chamber_raises(self) -> None:
        """The match rate gates #228, so a typo'd chamber must fail loudly rather than silently
        default to a two-year window and yield a quietly wrong number (CR finding 5)."""
        with pytest.raises(ValueError, match="unknown chamber"):
            audit_roster(records=[_record()], claims=[("John Wynne", 39, "hosue", 1991)])
