"""Default-tier tests for the refresh entrypoint."""

from __future__ import annotations

import os
from datetime import date
from pathlib import Path
from unittest.mock import patch

import pytest
import vcr
from sqlalchemy import select

from clearinghouse_core.provenance import Source
from clearinghouse_domain_legislative.identity import Organization
from usa_wa_adapter_legislature import refresh as refresh_module
from usa_wa_adapter_legislature.refresh import (
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
            db_session, biennium="2025-26", meeting_client=_FakeMeetingClient(_jtc_docket())
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
            db_session, biennium="2025-26", meeting_client=_FakeMeetingClient(_jtc_docket())
        )

    # Second invocation reuses the Source row; committees hit the cache (no new
    # SOAP call). The meeting pull is served by the injected fake either way —
    # #63 forces it only while 2025-26 is the date-current biennium.
    await run_refresh(
        db_session, biennium="2025-26", meeting_client=_FakeMeetingClient(_jtc_docket())
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
            first = await run_refresh(db_session, biennium="2025-26", meeting_client=meeting_client)

        # No cassette here: a committees SOAP call would error, so passing proves the
        # committees path cache-hit while the meeting pull re-fetched regardless.
        second = await run_refresh(db_session, biennium="2025-26", meeting_client=meeting_client)

    assert first.meetings_upserted == 1
    assert meeting_client.calls == 2
    assert second.meetings_upserted == 1
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
            first = await run_refresh(db_session, biennium="2025-26", meeting_client=meeting_client)
        second = await run_refresh(db_session, biennium="2025-26", meeting_client=meeting_client)

    assert first.meetings_upserted == 1
    assert meeting_client.calls == 1  # second run: TTL cache hit, no re-pull
    assert second.meetings_upserted == 0


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
                db_session, biennium="2025-26", meeting_client=_FakeMeetingClient(_jtc_docket())
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
                db_session, biennium="2025-26", meeting_client=_FakeMeetingClient(_jtc_docket())
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
