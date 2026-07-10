"""Default-tier tests for the refresh entrypoint."""

from __future__ import annotations

import os
from datetime import UTC, date, datetime
from pathlib import Path
from unittest.mock import patch

import pytest
import vcr
from sqlalchemy import select, text
from ulid import ULID as _ULID

from clearinghouse_core.jurisdictions import Jurisdiction
from clearinghouse_core.provenance import Citation, FetchEvent, FetchStatus, Source
from clearinghouse_core.runner import AdapterRunner
from clearinghouse_domain_legislative.identity import Assignment, Organization, Person
from usa_wa_adapter_legislature import refresh as refresh_module
from usa_wa_adapter_legislature.adapter import WALegislatureAdapter
from usa_wa_adapter_legislature.bootstrap import bootstrap_synthetic_anchors
from usa_wa_adapter_legislature.provisioning import (
    get_or_create_source,
    resolve_jurisdiction,
)
from usa_wa_adapter_legislature.refresh import (
    _discover_members,
    biennium_for_date,
    biennium_start_date,
    previous_biennium,
    run_refresh,
)
from usa_wa_adapter_legislature.transport import WireFetch

CASSETTE_DIR = Path(__file__).parent / "cassettes"
CASSETTE = "committee_service_get_active_committees_2025-26.yaml"


class _FakeMeetingClient:
    """Injectable CommitteeMeetingService stand-in (no network) for the daily-pull path."""

    def __init__(self, records: list[dict]) -> None:
        self._records = records
        self.calls = 0

    async def fetch_committee_meetings(self, begin, end) -> WireFetch:  # noqa: ANN001
        self.calls += 1
        return WireFetch(records=self._records, wire=b"<docket/>", content_type="text/xml")


class _FakeSponsorClient:
    """Injectable SponsorService stand-in (no network) — empty roster by default."""

    def __init__(self, records: list[dict] | None = None) -> None:
        self._records = records or []
        self.calls: list[str] = []

    async def fetch_sponsors(self, biennium) -> WireFetch:  # noqa: ANN001
        self.calls.append(biennium)
        return WireFetch(records=self._records, wire=b"<sponsors/>", content_type="text/xml")

    async def parse_sponsors(self, wire) -> list[dict]:  # noqa: ANN001
        # Archive-first span re-drive (#78-2c) re-parses the archived wire offline; the
        # fake returns its fixed roster (the wire bytes are opaque to the test).
        return self._records


class _FakeMembersClient:
    """Injectable GetCommitteeMembers stand-in (no network) — empty roster.

    #82: the daily fan-out keys the historical op by the current biennium, and the span
    re-drive re-parses the archived wire offline through the same binding."""

    def __init__(self, records: list[dict] | None = None) -> None:
        self._records = records or []
        self.calls: list[tuple[str, str, str]] = []

    async def fetch_historical_committee_members(self, biennium, agency, name) -> WireFetch:  # noqa: ANN001
        self.calls.append((biennium, agency, name))
        return WireFetch(records=self._records, wire=b"<members/>", content_type="text/xml")

    async def parse_historical_committee_members(self, wire) -> list[dict]:  # noqa: ANN001
        return self._records


def _jtc_docket() -> list[dict]:
    """A one-meeting docket carrying the Joint Transportation Committee (Id -140)."""
    return [
        {
            "Agency": "Joint",
            "Committees": {
                "Committee": [
                    {
                        "Id": -140,
                        "Name": "Joint Transportation Committee",
                        "LongName": "Joint Joint Transportation Committee",
                        "Agency": "Joint",
                        "Acronym": "JTC",
                        "Phone": None,
                    }
                ]
            },
        }
    ]


@pytest.mark.parametrize(
    "today,expected",
    [
        (date(2025, 1, 13), "2025-26"),
        (date(2025, 12, 31), "2025-26"),
        (date(2026, 6, 18), "2025-26"),
        (date(2026, 12, 31), "2025-26"),
        (date(2027, 1, 1), "2027-28"),
        (date(2030, 7, 4), "2029-30"),
    ],
)
def test_biennium_for_date_rolls_on_odd_years(today, expected):
    """WA bienniums start on odd years; even-year dates roll back to the start."""
    assert biennium_for_date(today) == expected


@pytest.mark.parametrize(
    "label,expected",
    [
        ("2025-26", date(2025, 1, 1)),
        ("2027-28", date(2027, 1, 1)),
        ("2099-00", date(2099, 1, 1)),
    ],
)
def test_biennium_start_date_is_jan1_of_the_odd_year(label, expected):
    """The window boundary for a rename = the biennium's start (Jan 1 of the odd year).

    WSL exposes no real name-change date, so the boundary is the documented
    biennium-start approximation."""
    assert biennium_start_date(label) == expected


@pytest.mark.parametrize(
    "label,expected",
    [
        ("2025-26", "2023-24"),
        ("2027-28", "2025-26"),
        ("2001-02", "1999-00"),
    ],
)
def test_previous_biennium_steps_back_two_years(label, expected):
    """The prior biennium is the rename diff's "before" side."""
    assert previous_biennium(label) == expected


async def test_run_refresh_seeds_source_and_runs_adapter(db_session, usa_wa):
    """The entrypoint lazy-creates Source, bootstraps anchors, runs refresh."""
    recorder = vcr.VCR(
        cassette_library_dir=str(CASSETTE_DIR),
        record_mode="none",
        match_on=["method", "scheme", "host", "port", "path"],
        decode_compressed_response=True,
    )
    with (
        recorder.use_cassette(CASSETTE),
        patch(
            "usa_wa_adapter_legislature.refresh.biennium_for_date",
            return_value="2025-26",
        ),
    ):
        outcome = await run_refresh(
            db_session,
            biennium="2025-26",
            meeting_client=_FakeMeetingClient(_jtc_docket()),
            sponsor_client=_FakeSponsorClient(),
            member_client=_FakeMembersClient(),
        )

    # The committees summary is unchanged by the additive meeting pull.
    assert outcome.committees.discovered == 1
    assert outcome.committees.fetched == 1
    assert outcome.committees.upserted_entities == 34
    assert outcome.committees.errors == 0
    # The additive meeting pull's upsert count is surfaced separately.
    assert outcome.meetings_upserted == 1

    sources = (await db_session.execute(select(Source))).scalars().all()
    assert len(sources) == 1
    assert sources[0].slug == "usa_wa_legislature"

    committees = (
        (await db_session.execute(select(Organization).where(Organization.org_type == "committee")))
        .scalars()
        .all()
    )
    assert len(committees) == 34

    # The additive current-window meeting pull produced the Joint body (org_type='other').
    others = (
        (await db_session.execute(select(Organization).where(Organization.org_type == "other")))
        .scalars()
        .all()
    )
    assert {o.source_id for o in others} == {"-140"}


async def test_refresh_builds_a_fill_only_runner(db_session, usa_wa, monkeypatch):
    """The refresh must run the AdapterRunner ``fill_only`` (#65): its discovery pull
    inserts new committees but never overwrites PM-curated ``name``/``acronym`` on
    existing rows (which would clobber curation + bump ``updated_at``, winning LWW)."""
    captured: dict = {}
    real_runner = refresh_module.AdapterRunner

    def _spy(*args, **kwargs):
        captured.update(kwargs)
        return real_runner(*args, **kwargs)

    monkeypatch.setattr(refresh_module, "AdapterRunner", _spy)
    recorder = vcr.VCR(
        cassette_library_dir=str(CASSETTE_DIR),
        record_mode="none",
        match_on=["method", "scheme", "host", "port", "path"],
        decode_compressed_response=True,
    )
    with recorder.use_cassette(CASSETTE):
        await run_refresh(
            db_session,
            biennium="2025-26",
            meeting_client=_FakeMeetingClient(_jtc_docket()),
            sponsor_client=_FakeSponsorClient(),
            member_client=_FakeMembersClient(),
        )

    assert captured.get("fill_only") is True


async def test_run_refresh_is_idempotent_on_source_creation(db_session, usa_wa):
    """A second call reuses the existing Source (no duplicate slug violation)."""
    recorder = vcr.VCR(
        cassette_library_dir=str(CASSETTE_DIR),
        record_mode="none",
        match_on=["method", "scheme", "host", "port", "path"],
        decode_compressed_response=True,
    )
    with recorder.use_cassette(CASSETTE):
        await run_refresh(
            db_session,
            biennium="2025-26",
            meeting_client=_FakeMeetingClient(_jtc_docket()),
            sponsor_client=_FakeSponsorClient(),
            member_client=_FakeMembersClient(),
        )

    # Second invocation reuses the Source row; committees hit the cache (no new
    # SOAP call). The meeting pull is served by the injected fake either way —
    # #63 forces it only while 2025-26 is the date-current biennium.
    await run_refresh(
        db_session,
        biennium="2025-26",
        meeting_client=_FakeMeetingClient(_jtc_docket()),
        sponsor_client=_FakeSponsorClient(),
        member_client=_FakeMembersClient(),
    )

    sources = (await db_session.execute(select(Source))).scalars().all()
    assert len(sources) == 1


async def test_meeting_pull_is_forced_while_committees_stay_ttl_governed(db_session, usa_wa):
    """A second refresh inside the cache TTL still pulls the meeting window (#63).

    The meeting pull exists for daily additive Joint/`Other` discovery (#39), but a
    24h TTL against the ~24h timer cadence made fetch-vs-skip depend on second-level
    jitter (effective cadence ~every other day). The pull is forced — deterministic
    daily — while the committees path stays TTL-governed for request frugality.
    """
    recorder = vcr.VCR(
        cassette_library_dir=str(CASSETTE_DIR),
        record_mode="none",
        match_on=["method", "scheme", "host", "port", "path"],
        decode_compressed_response=True,
    )
    meeting_client = _FakeMeetingClient(_jtc_docket())
    # Pin the wall clock's biennium so 2025-26 stays "current" when this runs in 2027+.
    with patch(
        "usa_wa_adapter_legislature.refresh.biennium_for_date",
        return_value="2025-26",
    ):
        with recorder.use_cassette(CASSETTE):
            first = await run_refresh(
                db_session,
                biennium="2025-26",
                meeting_client=meeting_client,
                sponsor_client=_FakeSponsorClient(),
                member_client=_FakeMembersClient(),
            )

        # No cassette here: a committees SOAP call would error, so passing proves the
        # committees path cache-hit while the meeting pull re-fetched regardless.
        second = await run_refresh(
            db_session,
            biennium="2025-26",
            meeting_client=meeting_client,
            sponsor_client=_FakeSponsorClient(),
            member_client=_FakeMembersClient(),
        )

    assert first.meetings_upserted == 1
    # The meeting pull re-fetched past the TTL (forced) — proven by the 2nd SOAP call...
    assert meeting_client.calls == 2
    # ...but the docket is byte-identical, so skip_unchanged skips the re-normalize (no
    # duplicate Citation set); the FetchEvent ledger still advanced. Forcedness is proven
    # by calls==2, not by a re-upsert.
    assert second.meetings_upserted == 0
    assert second.committees.skipped_cache_hit == 1
    assert second.committees.fetched == 0


async def test_meeting_pull_stays_ttl_governed_for_noncurrent_biennium(db_session, usa_wa):
    """A refresh pinned to a non-current biennium must not force the meeting pull (#63).

    ``USA_WA_BIENNIUM`` backfills point at closed windows — immutable history the
    harvest deliberately never re-pulls. The force applies only when the refreshed
    biennium is the date-current one; otherwise cache-or-fetch governs, so a
    same-TTL re-run costs no live docket pull.
    """
    recorder = vcr.VCR(
        cassette_library_dir=str(CASSETTE_DIR),
        record_mode="none",
        match_on=["method", "scheme", "host", "port", "path"],
        decode_compressed_response=True,
    )
    meeting_client = _FakeMeetingClient(_jtc_docket())
    # Wall clock says 2027-28, so the refreshed 2025-26 window is closed history.
    with patch(
        "usa_wa_adapter_legislature.refresh.biennium_for_date",
        return_value="2027-28",
    ):
        with recorder.use_cassette(CASSETTE):
            first = await run_refresh(
                db_session,
                biennium="2025-26",
                meeting_client=meeting_client,
                sponsor_client=_FakeSponsorClient(),
                member_client=_FakeMembersClient(),
            )
        second = await run_refresh(
            db_session,
            biennium="2025-26",
            meeting_client=meeting_client,
            sponsor_client=_FakeSponsorClient(),
            member_client=_FakeMembersClient(),
        )

    assert first.meetings_upserted == 1
    assert meeting_client.calls == 1  # second run: TTL cache hit, no re-pull
    assert second.meetings_upserted == 0


def _sponsor(id_, first, last, *, agency, party, district):
    return {
        "Id": id_,
        "Name": f"{first} {last}",
        "LongName": f"{'Senator' if agency == 'Senate' else 'Representative'} {last}",
        "Agency": agency,
        "Party": party,
        "District": district,
        "FirstName": first,
        "LastName": last,
    }


async def test_run_refresh_materializes_member_cluster(db_session, usa_wa):
    """The daily refresh, after committees, pulls sponsors + fans out committee members —
    materializing Persons + Assignments alongside the committee rows."""
    # Seed LD 18 so the senator's seat resolves.
    db_session.add(
        Jurisdiction(
            slug="usa-wa-ld-18",
            name="WA LD 18",
            type_id=usa_wa.type_id,
            pm_jurisdiction_id=_ULID(),
            recorded_at=datetime.now(UTC),
        )
    )
    await db_session.flush()

    recorder = vcr.VCR(
        cassette_library_dir=str(CASSETTE_DIR),
        record_mode="none",
        match_on=["method", "scheme", "host", "port", "path"],
        decode_compressed_response=True,
    )
    with (
        recorder.use_cassette(CASSETTE),
        patch("usa_wa_adapter_legislature.refresh.biennium_for_date", return_value="2025-26"),
    ):
        outcome = await run_refresh(
            db_session,
            biennium="2025-26",
            meeting_client=_FakeMeetingClient([]),  # empty docket
            sponsor_client=_FakeSponsorClient(
                [_sponsor(101, "Ann", "Rivers", agency="Senate", party="R", district="18")]
            ),
            member_client=_FakeMembersClient(
                [
                    _sponsor(
                        301, "Kristine", "Reeves", agency="House", party="Democrat", district="30"
                    )
                ]
            ),
        )

    assert outcome.members_upserted > 0
    assert outcome.member_spans > 0  # #78-2c: the daily refresh re-drove the span builder
    assert outcome.committee_spans > 0  # #82: and the committee-membership span builder
    persons = {p.source_id for p in (await db_session.execute(select(Person))).scalars().all()}
    assert "101" in persons  # from the sponsor pull
    assert "301" in persons  # from the committee-member fan-out
    # the senator got a seat Assignment; the committee member got membership Assignment(s)
    assignments = (await db_session.execute(select(Assignment))).scalars().all()
    dims = {a.source_id.split(":")[1] for a in assignments}
    assert "chamber-senate" in dims
    assert "committee" in dims
    # the Senate seat is now a merged SPAN (4-part source_id, open end), not a per-biennium row
    seat = next(a for a in assignments if ":chamber-senate:" in a.source_id)
    assert seat.source_id == "101:chamber-senate:18:2025-26"
    assert seat.valid_to is None and seat.is_active is True


async def test_span_rebuild_failure_is_isolated_by_savepoint(db_session, usa_wa):
    """A span-build failure rolls back only its SAVEPOINT — the committees refresh + the
    member pull still succeed, and member_spans is 0 (best-effort, #78-2c)."""
    db_session.add(
        Jurisdiction(
            slug="usa-wa-ld-18",
            name="WA LD 18",
            type_id=usa_wa.type_id,
            pm_jurisdiction_id=_ULID(),
            recorded_at=datetime.now(UTC),
        )
    )
    await db_session.flush()

    recorder = vcr.VCR(
        cassette_library_dir=str(CASSETTE_DIR),
        record_mode="none",
        match_on=["method", "scheme", "host", "port", "path"],
        decode_compressed_response=True,
    )
    with (
        recorder.use_cassette(CASSETTE),
        patch("usa_wa_adapter_legislature.refresh.biennium_for_date", return_value="2025-26"),
        patch(
            "usa_wa_adapter_legislature.refresh.build_sponsor_spans",
            side_effect=RuntimeError("span build boom"),
        ),
    ):
        outcome = await run_refresh(
            db_session,
            biennium="2025-26",
            meeting_client=_FakeMeetingClient([]),
            sponsor_client=_FakeSponsorClient(
                [_sponsor(101, "Ann", "Rivers", agency="Senate", party="R", district="18")]
            ),
            member_client=_FakeMembersClient([]),
        )

    assert outcome.member_spans == 0  # the build failed and was contained
    assert outcome.committees.errors == 0  # primary refresh unaffected
    assert outcome.members_upserted > 0  # the Person pull (before the span rebuild) survived
    # the session is still usable — the Person from the member pull persisted
    persons = {p.source_id for p in (await db_session.execute(select(Person))).scalars().all()}
    assert "101" in persons


async def _cite_committees(session, *, source, committees, resource_id):
    """Attach a ``resource_id`` citation to each committee via one shared FetchEvent —
    mirrors how a real GetActiveCommittees (``committees:<biennium>``) or roster
    (``committees-roster:<biennium>``) pull cites the orgs it produced. The #72 member
    fan-out scopes on the ``committees:<biennium>`` citation."""
    event = FetchEvent(
        source_id=source.id,
        resource_id=resource_id,
        url="https://wslwebservices.leg.wa.gov/CommitteeService.asmx#GetActiveCommittees",
        fetched_at=datetime.now(UTC),
        content_hash=b"\x00" * 32,
        status=FetchStatus.ok,
    )
    session.add(event)
    await session.flush()
    for committee in committees:
        session.add(
            Citation(
                entity_type="organization",
                entity_id=committee.id,
                fetch_event_id=event.id,
                field_path=None,
                confidence=1.0,
                asserted_at=event.fetched_at,
            )
        )
    await session.flush()


async def test_member_fanout_scoped_to_current_biennium_provenance(db_session, usa_wa):
    """The fan-out pulls only committees with a ``committees:<biennium>`` GetActiveCommittees
    citation — a historical backfill committee (``active=True`` but only
    ``committees-roster:*`` provenance) is excluded, so its members aren't mis-attributed and
    no wasted GetActiveCommitteeMembers Fault fires (#72)."""
    anchors = await bootstrap_synthetic_anchors(
        db_session, biennium="2025-26", jurisdiction_id=usa_wa.id
    )
    jurisdiction = await resolve_jurisdiction(db_session)
    source = await get_or_create_source(db_session, jurisdiction)
    current = Organization(
        source="usa_wa_legislature",
        source_id="CUR",
        jurisdiction_id=usa_wa.id,
        name="House Current",
        short_name="Current",
        org_type="committee",
        parent_organization_id=anchors.house_id,
        active=True,
    )
    historical = Organization(
        source="usa_wa_legislature",
        source_id="HIST",
        jurisdiction_id=usa_wa.id,
        name="House Historical",
        short_name="Historical",
        org_type="committee",
        parent_organization_id=anchors.house_id,
        active=True,  # backfill default — the old broken scope would have fanned this out
    )
    db_session.add_all([current, historical])
    await db_session.flush()
    await _cite_committees(
        db_session, source=source, committees=[current], resource_id="committees:2025-26"
    )
    await _cite_committees(
        db_session, source=source, committees=[historical], resource_id="committees-roster:2011-12"
    )

    member_client = _FakeMembersClient(
        [_sponsor(301, "Kristine", "Reeves", agency="House", party="Democrat", district="30")]
    )
    adapter = WALegislatureAdapter(
        anchors=anchors,
        jurisdiction_id=usa_wa.id,
        biennium="2025-26",
        sponsor_client=_FakeSponsorClient(),
        member_client=member_client,
        session=db_session,
    )
    runner = AdapterRunner(
        adapter,
        db_session,
        source=source,
        jurisdiction=jurisdiction,
        natural_key=("source", "source_id"),
        fill_only=True,
    )

    with patch("usa_wa_adapter_legislature.refresh.biennium_for_date", return_value="2025-26"):
        await _discover_members(runner, db_session, "2025-26", anchors)

    # Only the current-provenance committee was fanned out; the historical one was skipped.
    assert member_client.calls == [("2025-26", "House", "Current")]


class _PoisonNormalizeAdapter(WALegislatureAdapter):
    """Adapter whose committee-member ``normalize`` runs a **failing SQL** for one
    poison committee id — a genuine DB-layer error that leaves the connection in an
    aborted state (as a real IntegrityError/DataError during persist would). Absent the
    per-pull SAVEPOINT the shared transaction would be poisoned and every later pull +
    the final commit would raise ``PendingRollbackError`` (#1)."""

    def __init__(self, *, poison_committee_id: str, **kwargs) -> None:
        super().__init__(**kwargs)
        self._poison_committee_id = poison_committee_id

    async def normalize(self, payload):  # noqa: ANN001, ANN201
        if payload.url.endswith("#GetActiveCommitteeMembers"):
            committee_source_id = payload.url.split("committee_id=", 1)[1].split("#", 1)[0]
            if committee_source_id == self._poison_committee_id:
                await self._require_session().execute(text("SELECT 1 / 0"))  # aborts the tx
        return await super().normalize(payload)


async def test_member_fanout_db_error_is_isolated_by_savepoint(db_session, usa_wa):
    """A DB-layer failure in one committee's fan-out must not poison the shared
    transaction: the sponsor Persons and the surviving committee's memberships still
    commit (#1 — savepoint isolation, not just transport-error isolation)."""
    anchors = await bootstrap_synthetic_anchors(
        db_session, biennium="2025-26", jurisdiction_id=usa_wa.id
    )
    # Two House standing committees; one poisons its own normalize.
    committees = [
        Organization(
            source="usa_wa_legislature",
            source_id=cid,
            jurisdiction_id=usa_wa.id,
            name=f"House {short}",
            short_name=short,
            org_type="committee",
            parent_organization_id=anchors.house_id,
            active=True,
        )
        for cid, short in (("100", "Good"), ("200", "Poison"))
    ]
    db_session.add_all(committees)
    await db_session.flush()

    jurisdiction = await resolve_jurisdiction(db_session)
    source = await get_or_create_source(db_session, jurisdiction)
    # Both need current-biennium GetActiveCommittees provenance to be in the fan-out scope (#72).
    await _cite_committees(
        db_session, source=source, committees=committees, resource_id="committees:2025-26"
    )
    adapter = _PoisonNormalizeAdapter(
        anchors=anchors,
        jurisdiction_id=usa_wa.id,
        biennium="2025-26",
        sponsor_client=_FakeSponsorClient(
            [_sponsor(101, "Ann", "Rivers", agency="House", party="R", district="18")]
        ),
        member_client=_FakeMembersClient(
            [_sponsor(301, "Kristine", "Reeves", agency="House", party="Democrat", district="30")]
        ),
        session=db_session,
        poison_committee_id="200",
    )
    runner = AdapterRunner(
        adapter,
        db_session,
        source=source,
        jurisdiction=jurisdiction,
        natural_key=("source", "source_id"),
        fill_only=True,
    )

    with patch("usa_wa_adapter_legislature.refresh.biennium_for_date", return_value="2025-26"):
        total = await _discover_members(runner, db_session, "2025-26", anchors)

    assert total > 0  # the good committee still upserted despite the poison one
    # Session survived (a query would raise PendingRollbackError if the tx were poisoned).
    persons = {p.source_id for p in (await db_session.execute(select(Person))).scalars().all()}
    assert "101" in persons  # sponsor pull committed
    assert "301" in persons  # good committee's member committed
    # #82: the fan-out archives rosters + Persons only — the surviving committee's roster is
    # archived under the historical key. Membership itself is a merged span built by the
    # re-drive in run_refresh, so NO Assignment lands on this (fan-out-only) path.
    archived = {
        e.resource_id
        for e in (await db_session.execute(select(FetchEvent))).scalars().all()
        if e.resource_id.startswith("committee-members-hist:")
    }
    assert "committee-members-hist:2025-26:100:House:Good" in archived
    assert (await db_session.execute(select(Assignment))).scalars().all() == []


async def test_run_refresh_warns_exactly_when_biennium_not_current(db_session, usa_wa, caplog):
    """A non-current biennium run warns; the routine current-biennium run stays quiet (#63).

    Non-current runs are legitimate only for manual backfills / early-year pins. A
    stale ``USA_WA_BIENNIUM`` left in the timer's env would silently redirect daily
    discovery to a closed window — the warning is the operator's journal-greppable
    signal. The current-biennium branch must NOT warn, or every daily run becomes
    alert noise.
    """
    recorder = vcr.VCR(
        cassette_library_dir=str(CASSETTE_DIR),
        record_mode="none",
        match_on=["method", "scheme", "host", "port", "path"],
        decode_compressed_response=True,
    )
    # Wall clock says 2027-28 → the refreshed 2025-26 biennium is non-current.
    with patch(
        "usa_wa_adapter_legislature.refresh.biennium_for_date",
        return_value="2027-28",
    ):
        with recorder.use_cassette(CASSETTE), caplog.at_level("WARNING"):
            await run_refresh(
                db_session,
                biennium="2025-26",
                meeting_client=_FakeMeetingClient(_jtc_docket()),
                sponsor_client=_FakeSponsorClient(),
                member_client=_FakeMembersClient(),
            )
    assert "wsl_refresh_noncurrent_biennium" in caplog.text

    caplog.clear()
    # Wall clock agrees with the refreshed biennium → no warning.
    with patch(
        "usa_wa_adapter_legislature.refresh.biennium_for_date",
        return_value="2025-26",
    ):
        with caplog.at_level("WARNING"):
            await run_refresh(
                db_session,
                biennium="2025-26",
                meeting_client=_FakeMeetingClient(_jtc_docket()),
                sponsor_client=_FakeSponsorClient(),
                member_client=_FakeMembersClient(),
            )
    assert "wsl_refresh_noncurrent_biennium" not in caplog.text


async def test_run_refresh_raises_when_jurisdiction_missing(db_session):
    """A clean DB without the usa-wa jurisdiction row → explicit error."""
    with pytest.raises(LookupError, match="usa-wa"):
        await run_refresh(db_session, biennium="2025-26")


async def test_main_returns_2_when_database_url_unset(monkeypatch, capsys):
    """Missing DATABASE_URL → stderr message + exit code 2 (config error)."""
    monkeypatch.delenv("DATABASE_URL", raising=False)
    with patch.object(refresh_module, "configure_logging"):
        # Patched no-op: configure_logging mutates root-logger handlers
        # globally; leaving it untouched would persist a stdout JSON handler
        # for every subsequent test in the session.
        code = await refresh_module._main()
    assert code == 2
    captured = capsys.readouterr()
    assert "DATABASE_URL is not set" in captured.err


async def test_main_returns_1_when_run_refresh_raises(monkeypatch, capsys, test_engine):
    """An exception from run_refresh is caught, logged, and produces exit 1.

    Depends on ``test_engine`` (not ``db_session``) because we only need the
    schema setup side effect — ``_main`` opens its own engine against
    TEST_DATABASE_URL and ``run_refresh`` is patched to raise before any
    queries fire, so a savepointed session would be unused.
    """
    monkeypatch.setenv("DATABASE_URL", os.environ["TEST_DATABASE_URL"])

    async def boom(*_args, **_kwargs):
        raise RuntimeError("simulated WSL failure")

    with (
        patch.object(refresh_module, "configure_logging"),
        patch.object(refresh_module, "run_refresh", boom),
        patch.object(refresh_module.logger, "exception") as mock_exception,
    ):
        code = await refresh_module._main()

    assert code == 1
    mock_exception.assert_called_once_with("wsl_refresh_failed")
    # The success-path summary line must not have printed.
    captured = capsys.readouterr()
    assert "WSL refresh:" not in captured.out
