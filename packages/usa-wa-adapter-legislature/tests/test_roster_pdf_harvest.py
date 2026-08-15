"""Roster-PDF Phase A harvest + the read-only audit oracle (#225)."""

from __future__ import annotations

import httpx
import pytest
import respx
from sqlalchemy import select

from clearinghouse_core.provenance import FetchEvent
from usa_wa_adapter_legislature.roster_pdf.audit import (
    RosterAudit,
    audit_roster,
    match_rate,
)
from usa_wa_adapter_legislature.roster_pdf.harvest import harvest_roster
from usa_wa_adapter_legislature.roster_pdf.normalize import RosterRecord
from usa_wa_adapter_legislature.roster_pdf.provisioning import get_or_create_roster_source
from usa_wa_adapter_legislature.roster_pdf.transport import DEFAULT_ROSTER_URL


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


class TestHarvest:
    @respx.mock
    async def test_archives_the_edition(self, db_session, usa_wa, roster_pdf_bytes) -> None:
        respx.get(DEFAULT_ROSTER_URL).mock(
            return_value=httpx.Response(200, content=roster_pdf_bytes)
        )
        source = await get_or_create_roster_source(db_session, usa_wa)
        summary = await harvest_roster(db_session, revision="2025-06-05")
        assert summary.archived == 1
        events = (
            (await db_session.execute(select(FetchEvent).where(FetchEvent.source_id == source.id)))
            .scalars()
            .all()
        )
        assert [e.resource_id for e in events] == ["legroster:2025-06-05"]

    @respx.mock
    async def test_reharvest_is_a_cache_hit(self, db_session, usa_wa, roster_pdf_bytes) -> None:
        """The document changes ~biennially; re-running must not re-archive 5.7MB."""
        respx.get(DEFAULT_ROSTER_URL).mock(
            return_value=httpx.Response(200, content=roster_pdf_bytes)
        )
        await get_or_create_roster_source(db_session, usa_wa)
        assert (await harvest_roster(db_session, revision="2025-06-05")).archived == 1
        assert (await harvest_roster(db_session, revision="2025-06-05")).archived == 0

    @respx.mock
    async def test_unavailable_source_is_degraded_not_a_crash(self, db_session, usa_wa) -> None:
        """A rotated media key with no discoverable href needs an operator, so it must surface
        as a degraded run rather than an exception that loses the tally."""
        respx.get(DEFAULT_ROSTER_URL).mock(return_value=httpx.Response(404))
        respx.get("https://leg.wa.gov/about-the-legislature/legislative-information-center/").mock(
            return_value=httpx.Response(200, html="<a href='/media/x/budget.pdf'>B</a>")
        )
        await get_or_create_roster_source(db_session, usa_wa)
        summary = await harvest_roster(db_session, revision="2025-06-05")
        assert summary.archived == 0
        assert summary.unavailable is True


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
