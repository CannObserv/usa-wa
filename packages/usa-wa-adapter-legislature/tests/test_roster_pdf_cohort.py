"""Roster provisioning + archive-first cohort provider (#225)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from clearinghouse_core.provenance import FetchEvent, FetchStatus, RawPayload
from clearinghouse_core.source_coverage import SourceCoverage
from usa_wa_adapter_legislature.roster_pdf.adapter import roster_resource_id
from usa_wa_adapter_legislature.roster_pdf.cohort import RosterCohortProvider
from usa_wa_adapter_legislature.roster_pdf.coverage import ROSTER_SOURCE_SLUG
from usa_wa_adapter_legislature.roster_pdf.provisioning import get_or_create_roster_source

# The ``db`` marker is derived from the fixture closure (root conftest), not declared here.


async def _archive(db_session, source, revision, body, *, fetched_at=None, with_payload=True):
    event = FetchEvent(
        source_id=source.id,
        resource_id=roster_resource_id(revision),
        url="https://leg.wa.gov/media/x/members-of-the-legislature-1889-2025.pdf",
        fetched_at=fetched_at or datetime.now(UTC),
        http_status=200,
        content_hash=bytes(32),
        status=FetchStatus.ok,
    )
    db_session.add(event)
    await db_session.flush()
    if with_payload:
        db_session.add(
            RawPayload(
                fetch_event_id=event.id,
                content_type="application/pdf",
                body=body,
                size_bytes=len(body),
            )
        )
        await db_session.flush()
    return event


class TestProvisioning:
    async def test_creates_the_source_once_and_seeds_coverage(self, db_session, usa_wa) -> None:
        first = await get_or_create_roster_source(db_session, usa_wa)
        second = await get_or_create_roster_source(db_session, usa_wa)
        assert first.id == second.id
        assert first.slug == ROSTER_SOURCE_SLUG
        assert first.retention_policy.name == "archival"
        claims = (
            (
                await db_session.execute(
                    select(SourceCoverage).where(SourceCoverage.source_id == first.id)
                )
            )
            .scalars()
            .all()
        )
        assert [c.range_start for c in claims] == ["1889"]
        assert [c.range_end for c in claims] == ["2025"]

    async def test_is_a_distinct_source_from_the_wsl_soap_row(self, db_session, usa_wa) -> None:
        """Same jurisdiction and target, different publisher and archive — the multi-source
        pattern. Sharing the WSL Source row would conflate a daily API with a biennial PDF."""
        roster = await get_or_create_roster_source(db_session, usa_wa)
        assert roster.slug != "usa_wa_legislature"
        assert roster.kind == "document"


class TestCohortProvider:
    async def test_empty_archive_is_an_empty_report_not_a_crash(self, db_session, usa_wa) -> None:
        source = await get_or_create_roster_source(db_session, usa_wa)
        provider = RosterCohortProvider(session=db_session, source_id=source.id)
        assert await provider.citation_event() is None
        report = await provider.report()
        assert report.records == ()

    async def test_parses_the_archived_edition_offline(
        self, db_session, usa_wa, roster_pdf_bytes
    ) -> None:
        source = await get_or_create_roster_source(db_session, usa_wa)
        await _archive(db_session, source, "2025-06-05", roster_pdf_bytes)
        provider = RosterCohortProvider(session=db_session, source_id=source.id)
        records = await provider.records()
        assert records, "no records parsed from the archived PDF"
        assert {r.district for r in records} == {2}
        assert {r.chamber for r in records} == {"senate", "house"}

    async def test_latest_edition_must_be_payload_bearing(
        self, db_session, usa_wa, roster_pdf_bytes
    ) -> None:
        """A forced re-fetch re-records a payload-less FetchEvent when the bytes are identical.
        Ordering on FetchEvent alone would read the newest edition as an empty document."""
        source = await get_or_create_roster_source(db_session, usa_wa)
        now = datetime.now(UTC)
        await _archive(db_session, source, "2025-06-05", roster_pdf_bytes, fetched_at=now)
        await _archive(
            db_session,
            source,
            "2025-06-05",
            b"",
            fetched_at=now + timedelta(hours=1),
            with_payload=False,
        )
        provider = RosterCohortProvider(session=db_session, source_id=source.id)
        assert await provider.records(), "payload-less newest event shadowed the real edition"

    async def test_report_is_memoized(self, db_session, usa_wa, roster_pdf_bytes) -> None:
        source = await get_or_create_roster_source(db_session, usa_wa)
        await _archive(db_session, source, "2025-06-05", roster_pdf_bytes)
        provider = RosterCohortProvider(session=db_session, source_id=source.id)
        assert await provider.report() is await provider.report()
