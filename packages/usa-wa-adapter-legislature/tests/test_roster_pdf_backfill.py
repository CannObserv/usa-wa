"""Roster succession backfill — the write half (#226, epic #219 Phase 2).

The corpus already holds 124 hand-entered operator attestations, and the roster independently
reproduces 81 of them to the day. That overlap is what these tests are really about: a backfill
that walks over a human's attestation — or that stacks a second live boundary on one tenure —
is worse than one that writes nothing.
"""

from __future__ import annotations

from datetime import date

from sqlalchemy import select

from clearinghouse_domain_legislative.operator_events import OperatorEvent
from usa_wa_adapter_legislature.operators.store import (
    get_or_create_operator_source,
    record_operator_event,
)
from usa_wa_adapter_legislature.roster_pdf.backfill import (
    BACKFILL_ENTERED_BY,
    SKIP_ALREADY_ATTESTED,
    SKIP_CONFLICTS_WITH_ATTESTATION,
    roster_evidence_url,
    write_events,
)
from usa_wa_adapter_legislature.roster_pdf.cohort import RosterCohortProvider
from usa_wa_adapter_legislature.roster_pdf.normalize import RosterRecord
from usa_wa_adapter_legislature.roster_pdf.resolve import ResolvedEvent
from usa_wa_adapter_legislature.roster_pdf.succession import propose_events
from usa_wa_adapter_legislature.roster_pdf.transport import DEFAULT_ROSTER_URL
from usa_wa_common.jurisdiction import resolve_jurisdiction


def _resolved(
    annotation: str,
    *,
    member_id: str = "18517",
    seat_kind: str | None = None,
    seat_discriminator: str | None = None,
    **kw,
) -> ResolvedEvent:
    base = dict(
        district=2,
        chamber="senate",
        year=2013,
        order=1,
        name="Graham Hunt",
        party_token="R",
        page_number=104,
    )
    base.update(kw)
    report = propose_events([RosterRecord(annotation=annotation, **base)])
    (proposal,) = report.proposals + report.unseated
    return ResolvedEvent(
        member_id=member_id,
        kind=proposal.kind,
        reason=proposal.reason,
        effective_date=proposal.effective_date,
        seat_kind=seat_kind if seat_kind is not None else proposal.seat_kind,
        seat_discriminator=(
            seat_discriminator if seat_discriminator is not None else proposal.seat_discriminator
        ),
        proposal=proposal,
    )


async def _source(session):
    return await get_or_create_operator_source(session, await resolve_jurisdiction(session))


class TestEvidence:
    def test_the_evidence_url_points_at_the_source_page(self) -> None:
        """An operator adjudicating a machine-written boundary needs the page, not the
        document — the roster is 233 pages."""
        assert roster_evidence_url(104).endswith("#page=104")
        assert "members-of-the-legislature" in roster_evidence_url(104)


class TestWriting:
    async def test_writes_a_new_event_marked_as_machine_derived(self, db_session, usa_wa) -> None:
        """``entered_by`` separates a derived boundary from a human attestation, so a later
        audit can tell which is which without re-deriving anything."""
        source = await _source(db_session)
        summary = await write_events(
            db_session, source, [_resolved("Deceased June 15, 1979", chamber="senate")]
        )
        assert summary.written == 1
        row = (await db_session.execute(select(OperatorEvent))).scalar_one()
        assert row.member_id == "18517"
        assert row.kind == "departed"
        assert row.effective_date == date(1979, 6, 15)
        assert row.entered_by == BACKFILL_ENTERED_BY
        assert row.evidence_url.endswith("#page=104")

    async def test_a_second_run_writes_nothing(self, db_session, usa_wa) -> None:
        """Idempotent: the roster does not change, so re-running must be a no-op."""
        source = await _source(db_session)
        events = [_resolved("Deceased June 15, 1979", chamber="senate")]
        assert (await write_events(db_session, source, events)).written == 1
        second = await write_events(db_session, source, events)
        assert second.written == 0
        assert second.skipped[SKIP_ALREADY_ATTESTED] == 1

    async def test_writes_a_house_seat_with_its_resolved_position(self, db_session, usa_wa) -> None:
        source = await _source(db_session)
        await write_events(
            db_session,
            source,
            [
                _resolved(
                    "Appointed January 17, 2014 to serve unexpired term",
                    chamber="house",
                    seat_kind="chamber-house",
                    seat_discriminator="ld-2-position-1",
                )
            ],
        )
        row = (await db_session.execute(select(OperatorEvent))).scalar_one()
        assert (row.seat_kind, row.seat_discriminator) == ("chamber-house", "ld-2-position-1")


class TestAttestationSafety:
    async def test_an_existing_attestation_is_never_overwritten(self, db_session, usa_wa) -> None:
        """The roster reproduces 81 hand-entered events exactly. Re-writing them would replace
        a human's ``entered_by`` and evidence URL with the backfill's — losing who attested to
        the fact, in a store whose whole point is that provenance is never mutated (#54)."""
        source = await _source(db_session)
        await record_operator_event(
            db_session,
            source,
            member_id="18517",
            kind="departed",
            reason="died",
            effective_date=date(1979, 6, 15),
            evidence_url="https://example.gov/obituary",
            entered_by="gregoryfoster",
        )
        summary = await write_events(
            db_session, source, [_resolved("Deceased June 15, 1979", chamber="senate")]
        )
        assert summary.written == 0
        assert summary.skipped[SKIP_ALREADY_ATTESTED] == 1
        row = (await db_session.execute(select(OperatorEvent))).scalar_one()
        assert row.entered_by == "gregoryfoster"
        assert row.evidence_url == "https://example.gov/obituary"

    async def test_a_disagreeing_date_on_the_same_tenure_is_skipped_not_stacked(
        self, db_session, usa_wa
    ) -> None:
        """17 roster boundaries disagree with a hand-entered date on the same tenure, by 1 to
        41 days. Both would be live and non-superseded, giving one seat two boundaries — so
        the conflict is reported for adjudication rather than written."""
        source = await _source(db_session)
        await record_operator_event(
            db_session,
            source,
            member_id="18517",
            kind="departed",
            reason="resigned",
            effective_date=date(1979, 7, 20),
            evidence_url="https://example.gov/news",
            entered_by="gregoryfoster",
        )
        summary = await write_events(
            db_session, source, [_resolved("Deceased June 15, 1979", chamber="senate")]
        )
        assert summary.written == 0
        assert summary.skipped[SKIP_CONFLICTS_WITH_ATTESTATION] == 1
        assert len((await db_session.execute(select(OperatorEvent))).scalars().all()) == 1

    async def test_the_conflicts_are_reported_with_both_dates(self, db_session, usa_wa) -> None:
        """A skipped conflict that says only "skipped" cannot be adjudicated."""
        source = await _source(db_session)
        await record_operator_event(
            db_session,
            source,
            member_id="18517",
            kind="departed",
            reason="resigned",
            effective_date=date(1979, 7, 20),
            evidence_url="https://example.gov/news",
            entered_by="gregoryfoster",
        )
        summary = await write_events(
            db_session, source, [_resolved("Deceased June 15, 1979", chamber="senate")]
        )
        (conflict,) = summary.conflicts
        assert conflict.roster_date == date(1979, 6, 15)
        assert conflict.attested_date == date(1979, 7, 20)
        assert conflict.attested_by == "gregoryfoster"
        assert conflict.member_id == "18517"

    async def test_a_separate_tenure_in_another_biennium_still_writes(
        self, db_session, usa_wa
    ) -> None:
        """A gap-and-return member genuinely has two seatings on one seat. Scoping the conflict
        check to the biennium keeps that legitimate second boundary writable."""
        source = await _source(db_session)
        await record_operator_event(
            db_session,
            source,
            member_id="18517",
            kind="seated",
            reason="appointed",
            effective_date=date(2003, 5, 1),
            seat_kind="chamber-senate",
            seat_discriminator="2",
            evidence_url="https://example.gov/news",
            entered_by="gregoryfoster",
        )
        summary = await write_events(
            db_session,
            source,
            [
                _resolved(
                    "Appointed January 17, 2014 to serve unexpired term",
                    chamber="senate",
                    year=2013,
                )
            ],
        )
        assert summary.written == 1

    async def test_a_different_seat_is_not_a_conflict(self, db_session, usa_wa) -> None:
        """A chamber move puts two seatings on one member in one biennium — different seats,
        both real."""
        source = await _source(db_session)
        await record_operator_event(
            db_session,
            source,
            member_id="18517",
            kind="seated",
            reason="appointed",
            effective_date=date(2014, 1, 2),
            seat_kind="chamber-house",
            seat_discriminator="ld-2-position-1",
            evidence_url="https://example.gov/news",
            entered_by="gregoryfoster",
        )
        summary = await write_events(
            db_session,
            source,
            [
                _resolved(
                    "Appointed January 17, 2014 to serve unexpired term",
                    chamber="senate",
                    year=2013,
                )
            ],
        )
        assert summary.written == 1

    async def test_a_superseded_attestation_does_not_block_a_write(
        self, db_session, usa_wa
    ) -> None:
        """A superseded row is a retracted fact. Treating it as live would let a corrected-away
        boundary permanently block the roster from supplying the right one."""
        source = await _source(db_session)
        prior = await record_operator_event(
            db_session,
            source,
            member_id="18517",
            kind="departed",
            reason="resigned",
            effective_date=date(1979, 7, 20),
            evidence_url="https://example.gov/news",
            entered_by="gregoryfoster",
        )
        prior.superseded_by_id = prior.id
        await db_session.flush()
        summary = await write_events(
            db_session, source, [_resolved("Deceased June 15, 1979", chamber="senate")]
        )
        assert summary.written == 1


class TestSupersedingConflicts:
    """`--supersede-conflicts` (#226 adjudication): the roster wins, deliberately and only
    over a *machine*-entered attestation.

    All 17 live conflicts turned out to be agent-entered rows citing Wikipedia/Ballotpedia,
    and 5 of the 9 conflicting departures were dated to the **successor's seating date** —
    collapsing "incumbent departed" and "successor seated" into one date and asserting a
    zero-day vacancy where 1–29 days actually elapsed. Against the Legislature's own roster
    that is a defect, not a difference of opinion.
    """

    async def _prior(self, session, source, *, entered_by: str):
        return await record_operator_event(
            session,
            source,
            member_id="18517",
            kind="departed",
            reason="resigned",
            effective_date=date(1979, 7, 20),
            evidence_url="https://en.wikipedia.org/wiki/Someone_Else",
            entered_by=entered_by,
        )

    async def test_supersedes_a_machine_entered_conflict(self, db_session, usa_wa) -> None:
        source = await _source(db_session)
        prior = await self._prior(db_session, source, entered_by="exedev")
        summary = await write_events(
            db_session,
            source,
            [_resolved("Deceased June 15, 1979", chamber="senate")],
            supersede_conflicts=True,
        )
        assert summary.superseded == 1
        assert summary.written == 0
        assert prior.superseded_by_id is not None
        rows = (await db_session.execute(select(OperatorEvent))).scalars().all()
        live = [r for r in rows if r.superseded_by_id is None]
        assert len(live) == 1
        assert live[0].effective_date == date(1979, 6, 15)
        assert live[0].entered_by == BACKFILL_ENTERED_BY

    async def test_the_superseded_row_survives_for_audit(self, db_session, usa_wa) -> None:
        """Provenance is appended, never mutated (#54): the retracted attestation stays on
        record pointing at what replaced it."""
        source = await _source(db_session)
        prior = await self._prior(db_session, source, entered_by="exedev")
        await write_events(
            db_session,
            source,
            [_resolved("Deceased June 15, 1979", chamber="senate")],
            supersede_conflicts=True,
        )
        assert prior.effective_date == date(1979, 7, 20)
        assert prior.entered_by == "exedev"
        assert prior.evidence_url == "https://en.wikipedia.org/wiki/Someone_Else"

    async def test_a_human_attestation_is_never_superseded(self, db_session, usa_wa) -> None:
        """The allowlist is the whole safety property. A named operator's judgement is not
        the backfill's to overrule, however authoritative the roster is."""
        source = await _source(db_session)
        prior = await self._prior(db_session, source, entered_by="gregoryfoster")
        summary = await write_events(
            db_session,
            source,
            [_resolved("Deceased June 15, 1979", chamber="senate")],
            supersede_conflicts=True,
        )
        assert summary.superseded == 0
        assert summary.skipped[SKIP_CONFLICTS_WITH_ATTESTATION] == 1
        assert prior.superseded_by_id is None

    async def test_superseding_is_off_by_default(self, db_session, usa_wa) -> None:
        source = await _source(db_session)
        prior = await self._prior(db_session, source, entered_by="exedev")
        summary = await write_events(
            db_session, source, [_resolved("Deceased June 15, 1979", chamber="senate")]
        )
        assert summary.superseded == 0
        assert summary.skipped[SKIP_CONFLICTS_WITH_ATTESTATION] == 1
        assert prior.superseded_by_id is None

    async def test_an_identical_date_is_still_a_no_op(self, db_session, usa_wa) -> None:
        """An already-attested boundary has nothing to correct — superseding it would append
        a row identical to the one it retracts and strip a human's name off the live copy."""
        source = await _source(db_session)
        await record_operator_event(
            db_session,
            source,
            member_id="18517",
            kind="departed",
            reason="died",
            effective_date=date(1979, 6, 15),
            evidence_url="https://example.gov/obituary",
            entered_by="gregoryfoster",
        )
        summary = await write_events(
            db_session,
            source,
            [_resolved("Deceased June 15, 1979", chamber="senate")],
            supersede_conflicts=True,
        )
        assert (summary.superseded, summary.written) == (0, 0)
        assert summary.skipped[SKIP_ALREADY_ATTESTED] == 1
        row = (await db_session.execute(select(OperatorEvent))).scalar_one()
        assert row.entered_by == "gregoryfoster"


class TestMultiplePriorAttestations:
    """CR-4 finding 24: only `prior[0]` was examined, so a tenure holding two live
    attestations kept the others — the "one seat, two live boundaries" state this module
    exists to prevent. No such rows exist in prod today; this pins the latent case.
    """

    async def _attest(self, session, source, *, day: int, entered_by: str):
        return await record_operator_event(
            session,
            source,
            member_id="18517",
            kind="departed",
            reason="resigned",
            effective_date=date(1979, 7, day),
            evidence_url=f"https://example.gov/{day}",
            entered_by=entered_by,
        )

    async def test_every_disagreeing_row_is_reported(self, db_session, usa_wa) -> None:
        """An operator adjudicating needs to see each disagreeing row, not just the first."""
        source = await _source(db_session)
        await self._attest(db_session, source, day=20, entered_by="exedev")
        await self._attest(db_session, source, day=25, entered_by="exedev")
        summary = await write_events(
            db_session, source, [_resolved("Deceased June 15, 1979", chamber="senate")]
        )
        assert summary.written == 0
        assert {c.attested_date for c in summary.conflicts} == {
            date(1979, 7, 20),
            date(1979, 7, 25),
        }

    async def test_all_machine_rows_are_superseded_not_just_the_first(
        self, db_session, usa_wa
    ) -> None:
        source = await _source(db_session)
        first = await self._attest(db_session, source, day=20, entered_by="exedev")
        second = await self._attest(db_session, source, day=25, entered_by="exedev")
        summary = await write_events(
            db_session,
            source,
            [_resolved("Deceased June 15, 1979", chamber="senate")],
            supersede_conflicts=True,
        )
        assert summary.superseded == 1
        assert first.superseded_by_id is not None
        assert second.superseded_by_id is not None
        live = [
            r
            for r in (await db_session.execute(select(OperatorEvent))).scalars().all()
            if r.superseded_by_id is None
        ]
        assert len(live) == 1
        assert live[0].effective_date == date(1979, 6, 15)

    async def test_one_human_row_blocks_the_whole_tenure(self, db_session, usa_wa) -> None:
        """Half-correcting a tenure is worse than not correcting it: superseding the machine
        row while leaving the human's would still leave two live boundaries, and would have
        silently discarded the machine row's evidence for nothing."""
        source = await _source(db_session)
        machine = await self._attest(db_session, source, day=20, entered_by="exedev")
        human = await self._attest(db_session, source, day=25, entered_by="gregoryfoster")
        summary = await write_events(
            db_session,
            source,
            [_resolved("Deceased June 15, 1979", chamber="senate")],
            supersede_conflicts=True,
        )
        assert summary.superseded == 0
        assert summary.skipped[SKIP_CONFLICTS_WITH_ATTESTATION] == 1
        assert machine.superseded_by_id is None
        assert human.superseded_by_id is None


class TestEvidenceUrlDerivation:
    """CR-4 finding 28: the citation must name the URL the bytes actually came from."""

    def test_the_page_anchor_rides_the_supplied_base(self) -> None:
        """`s4gf4suc` is a CMS-minted media key the transport already expects to rotate; a
        citation hardcoded to the old URL is dead the moment it does, while the archived
        bytes remain fine."""
        assert (
            roster_evidence_url(104, base_url="https://leg.wa.gov/media/newkey/members.pdf")
            == "https://leg.wa.gov/media/newkey/members.pdf#page=104"
        )

    def test_it_still_defaults_to_the_known_url(self) -> None:
        assert roster_evidence_url(104).endswith("#page=104")


class TestSupersedingLeavesNoStaleIndexEntry:
    """CR-5 finding 31: the post-supersede bookkeeping filtered only `prior[0]`, so with more
    than one prior the other retracted rows survived in the scope index as though live. The
    round-4 tests missed it because each passed a single event; it takes a *second* boundary on
    the same tenure in the same batch to observe.
    """

    async def test_a_second_boundary_does_not_see_a_retracted_row_as_live(
        self, db_session, usa_wa
    ) -> None:
        source = await _source(db_session)
        for day in (20, 25):
            await record_operator_event(
                db_session,
                source,
                member_id="18517",
                kind="departed",
                reason="resigned",
                effective_date=date(1979, 7, day),
                evidence_url=f"https://example.gov/{day}",
                entered_by="exedev",
            )
        # Two roster boundaries on the same tenure+biennium. The first supersedes both priors;
        # the second must collide with what the first wrote, not with a row already retracted.
        summary = await write_events(
            db_session,
            source,
            [
                _resolved("Deceased June 15, 1979", chamber="senate"),
                _resolved("Resigned June 20, 1979", chamber="senate"),
            ],
            supersede_conflicts=True,
        )
        assert summary.superseded == 1
        rows = (await db_session.execute(select(OperatorEvent))).scalars().all()
        live = [r for r in rows if r.superseded_by_id is None]
        # Exactly one live boundary on the tenure — the whole point of the conflict machinery.
        assert len(live) == 1, [(r.effective_date, r.entered_by) for r in live]
        assert live[0].effective_date == date(1979, 6, 15)
        # The observable symptom of the stale entry is a *phantom conflict*: the second
        # boundary reported against a row the first boundary had already retracted. Expect
        # exactly three — both priors for the first event, then only the live row for the
        # second.
        assert [c.attested_date for c in summary.conflicts] == [
            date(1979, 7, 20),
            date(1979, 7, 25),
            date(1979, 6, 15),
        ]


class TestArchivedRosterUrl:
    """CR-5 findings 34/35: the citation base comes from the archived edition, and the
    latest-edition rule lives in exactly one place."""

    async def test_reads_the_url_the_bytes_came_from(
        self, db_session, usa_wa, roster_pdf_bytes
    ) -> None:
        import httpx
        import respx

        from usa_wa_adapter_legislature.roster_pdf.harvest import harvest_roster
        from usa_wa_adapter_legislature.roster_pdf.provisioning import get_or_create_roster_source

        rotated = "https://leg.wa.gov/media/rotatedkey/members-of-the-legislature-1889-2025.pdf"
        with respx.mock:
            respx.get(DEFAULT_ROSTER_URL).mock(return_value=httpx.Response(404))
            respx.get(
                "https://leg.wa.gov/about-the-legislature/legislative-information-center/"
            ).mock(return_value=httpx.Response(200, html=f"<a href='{rotated}'>roster</a>"))
            respx.get(rotated).mock(return_value=httpx.Response(200, content=roster_pdf_bytes))
            await harvest_roster(db_session, revision="2025-06-05")

        source = await get_or_create_roster_source(db_session, usa_wa)
        provider = RosterCohortProvider(session=db_session, source_id=source.id)
        assert await provider.archived_url() == rotated

    async def test_no_archive_yields_no_url(self, db_session, usa_wa) -> None:
        """The caller falls back to the compiled-in default only here — and with nothing
        archived the backfill has nothing to write anyway, so no wrong citation is minted."""
        from usa_wa_adapter_legislature.roster_pdf.provisioning import get_or_create_roster_source

        source = await get_or_create_roster_source(db_session, usa_wa)
        provider = RosterCohortProvider(session=db_session, source_id=source.id)
        assert await provider.archived_url() is None
