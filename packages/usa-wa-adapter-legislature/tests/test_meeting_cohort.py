"""Meeting-derived committee cohort builder (#56).

The #56 rename detector diffs two bienniums' Joint/`Other` cohorts. This module turns a
biennium label into ``{source_id: name}`` where ``name`` is the clean ``Name`` PM should
receive (#61's ``observed_name``: the clean ``Name`` only, never the double-prefixed
``LongName``) — so detection and the dated-name evidence #56 emits use the same string.
House/Senate refs and dormancy are the normalizer's concern (reused via
``joint_other_refs``)."""

from __future__ import annotations

from datetime import UTC, datetime

from clearinghouse_core.provenance import FetchEvent, FetchStatus, RawPayload, Source
from usa_wa_adapter_legislature.meeting_cohort import (
    MeetingCohortProvider,
    cohort_name,
    meeting_cohort_names,
)
from usa_wa_adapter_legislature.meeting_windows import biennium_window, meetings_resource_id
from usa_wa_adapter_legislature.transport import WireFetch


def _ref(cid, *, name, long_name, agency="Joint"):
    return {"Id": cid, "Name": name, "LongName": long_name, "Agency": agency}


def _meeting(refs):
    return {"Committees": {"Committee": refs}}


def test_cohort_name_prefers_clean_name_over_double_prefixed_longname():
    assert (
        cohort_name(_ref(-140, name="Joint Transportation Committee", long_name="Joint Joint X"))
        == "Joint Transportation Committee"
    )


def test_cohort_name_does_not_fall_back_to_double_prefixed_longname():
    """A blank Name yields None — never the double-prefixed LongName (which would reach PM)."""
    assert cohort_name(_ref(-140, name="   ", long_name="Other Statute Law Committee")) is None


def test_cohort_name_none_when_name_blank():
    assert cohort_name({"Id": 1, "Name": None, "LongName": "Joint X", "Agency": "Joint"}) is None


def test_meeting_cohort_names_keys_clean_names_by_source_id():
    cohort = meeting_cohort_names(
        [
            _meeting(
                [
                    _ref(-140, name="Joint Transportation Committee", long_name="Joint Joint X"),
                    _ref(31649, name="Finance", long_name="House Finance", agency="House"),
                ]
            )
        ]
    )
    assert cohort == {"-140": "Joint Transportation Committee"}  # House dropped


def test_meeting_cohort_names_drops_unnamed_ref():
    cohort = meeting_cohort_names(
        [_meeting([{"Id": 7, "Name": None, "LongName": None, "Agency": "Joint"}])]
    )
    assert cohort == {}


def test_meeting_cohort_names_drops_ref_without_id():
    """A Joint ref with no Id can't be a natural key — dropped (no crash)."""
    cohort = meeting_cohort_names(
        [_meeting([{"Name": "Nameless", "LongName": "Joint Nameless", "Agency": "Joint"}])]
    )
    assert cohort == {}


class _RecordingMeetingClient:
    """Stub CommitteeMeetingService client recording which path served the cohort.

    Tracks ``windows`` (live ``fetch_committee_meetings`` calls) vs ``parsed`` (offline
    ``parse_committee_meetings`` of an archived wire), so a test can assert the cache-first
    routing without touching zeep/network."""

    def __init__(self, *, fetch_records=None, parse_records=None):
        self._fetch_records = fetch_records or []
        self._parse_records = parse_records or []
        self.windows: list[tuple[datetime, datetime]] = []
        self.parsed: list[bytes] = []

    async def fetch_committee_meetings(self, begin, end):
        self.windows.append((begin, end))
        return WireFetch(records=self._fetch_records, wire=b"LIVE", content_type="text/xml")

    async def parse_committee_meetings(self, wire):
        self.parsed.append(wire)
        return self._parse_records


def _jtc_meeting():
    return [
        _meeting([_ref(-140, name="Joint Transportation Committee", long_name="Joint Joint X")])
    ]


async def _add_source(session, jurisdiction):
    src = Source(
        jurisdiction_id=jurisdiction.id, name="WSL", slug="usa_wa_legislature", kind="soap"
    )
    session.add(src)
    await session.flush()
    return src


async def _archive_window(session, source, biennium, body):
    begin, end = biennium_window(biennium)
    ev = FetchEvent(
        source_id=source.id,
        resource_id=meetings_resource_id(begin, end),
        url="https://wslwebservices.leg.wa.gov/CommitteeMeetingService.asmx",
        fetched_at=datetime.now(UTC),
        status=FetchStatus.ok,
    )
    session.add(ev)
    await session.flush()
    session.add(
        RawPayload(fetch_event_id=ev.id, content_type="text/xml", body=body, size_bytes=len(body))
    )
    await session.flush()


async def test_provider_without_session_always_pulls_live():
    client = _RecordingMeetingClient(fetch_records=_jtc_meeting())
    provider = MeetingCohortProvider(client)

    cohort = await provider.cohort("2023-24")

    assert cohort == {"-140": "Joint Transportation Committee"}
    assert client.parsed == []  # no cache without a session
    (begin, end) = client.windows[0]
    assert (begin.year, begin.month, begin.day) == (2023, 1, 1)
    assert (end.year, end.month, end.day) == (2024, 12, 31)


async def test_provider_reads_archived_window_instead_of_pulling(db_session, usa_wa):
    """An archived window is re-parsed offline — no live WSL pull for the immutable docket."""
    src = await _add_source(db_session, usa_wa)
    await _archive_window(db_session, src, "2023-24", b"<archived-soap/>")
    client = _RecordingMeetingClient(parse_records=_jtc_meeting())
    provider = MeetingCohortProvider(client, session=db_session, source_id=src.id)

    cohort = await provider.cohort("2023-24")

    assert cohort == {"-140": "Joint Transportation Committee"}
    assert client.parsed == [b"<archived-soap/>"]  # re-parsed the stored wire
    assert client.windows == []  # never re-pulled


async def test_provider_falls_back_to_live_when_window_unarchived(db_session, usa_wa):
    """A window with no archived copy (cold cache) pulls live — and is left un-archived."""
    src = await _add_source(db_session, usa_wa)  # source exists, but no payload for this window
    client = _RecordingMeetingClient(fetch_records=_jtc_meeting())
    provider = MeetingCohortProvider(client, session=db_session, source_id=src.id)

    cohort = await provider.cohort("2099-00")

    assert cohort == {"-140": "Joint Transportation Committee"}
    assert client.parsed == []
    assert len(client.windows) == 1  # live fallback
