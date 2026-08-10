"""Behaviour of the ``/api/v1`` operations slice (#184) against a real database.

The two questions these routes exist to answer are "did the job run?" (#178) and
"what does this feed actually cover?" (#180), and both have an *empty* answer that
is a legitimate finding rather than an error. Most of what is pinned here is that
the empty answer stays distinguishable from the other empty answers.
"""

from datetime import UTC, datetime, timedelta

import pytest
from ulid import ULID

from clearinghouse_core.provenance import Citation, FetchEvent, FetchStatus, Source
from clearinghouse_core.runs import JobRun
from clearinghouse_core.source_coverage import SourceCoverage

BASE = datetime(2026, 1, 1, tzinfo=UTC)


def _ordered_ulid(offset: int) -> ULID:
    """A ULID with a deterministic sort position — pagination is keyed on it."""
    return ULID.from_datetime(BASE + timedelta(days=offset))


@pytest.fixture
async def source(db_session, usa_wa) -> Source:
    row = Source(
        id=_ordered_ulid(1),
        jurisdiction_id=usa_wa.id,
        name="WA SOS filings",
        slug="wa_sos_filings",
        kind="csv",
        base_url="https://example.invalid/filings",
    )
    db_session.add(row)
    await db_session.flush()
    return row


class TestJobHealth:
    async def test_an_empty_ledger_is_an_empty_page_not_an_error(self, client):
        """#178 shipped with one adopter; the sweep is #179b. Nothing to report is
        the honest answer, and it must not look like a broken route."""
        response = await client.get("/api/v1/health/jobs")
        assert response.status_code == 200
        assert response.json() == {"items": [], "limit": 50, "next_cursor": None}

    async def test_reports_only_the_latest_run_per_slug(self, client, db_session):
        for day, outcome in ((1, "failed"), (2, "ok")):
            db_session.add(
                JobRun(
                    job_slug="integrity-sweep",
                    started_at=BASE + timedelta(days=day),
                    finished_at=BASE + timedelta(days=day, minutes=5),
                    outcome=outcome,
                    counters={"scanned": day},
                )
            )
        await db_session.flush()

        body = (await client.get("/api/v1/health/jobs")).json()
        assert len(body["items"]) == 1
        assert body["items"][0]["outcome"] == "ok"
        assert body["items"][0]["counters"] == {"scanned": 2}

    async def test_a_run_that_never_reported_back_is_in_flight_not_failed(self, client, db_session):
        """``finished_at IS NULL`` is the state a write-at-the-end ledger cannot
        represent, and it is not the same as ``failed``."""
        db_session.add(JobRun(job_slug="sos-results-harvest", started_at=BASE))
        await db_session.flush()

        item = (await client.get("/api/v1/health/jobs")).json()["items"][0]
        assert item["in_flight"] is True
        assert item["outcome"] is None
        assert item["finished_at"] is None
        assert item["duration_seconds"] is None

    async def test_degraded_survives_as_its_own_outcome(self, client, db_session):
        """``degraded`` sits between ok and failed — a job that completed but whose
        work did not land. Flattening it to failed loses the distinction #178 made."""
        db_session.add(
            JobRun(
                job_slug="wsl-daily-refresh",
                started_at=BASE,
                finished_at=BASE + timedelta(minutes=1),
                outcome="degraded",
            )
        )
        await db_session.flush()

        assert (await client.get("/api/v1/health/jobs")).json()["items"][0]["outcome"] == "degraded"

    async def test_pages_by_job_slug(self, client, db_session):
        for slug in ("a-job", "b-job", "c-job"):
            db_session.add(JobRun(job_slug=slug, started_at=BASE))
        await db_session.flush()

        first = (await client.get("/api/v1/health/jobs?limit=2")).json()
        assert [i["job_slug"] for i in first["items"]] == ["a-job", "b-job"]
        assert first["next_cursor"] == "b-job"

        second = (
            await client.get(f"/api/v1/health/jobs?limit=2&cursor={first['next_cursor']}")
        ).json()
        assert [i["job_slug"] for i in second["items"]] == ["c-job"]
        assert second["next_cursor"] is None

    async def test_run_id_is_base32_not_uuid_hex(self, client, db_session):
        db_session.add(JobRun(job_slug="integrity-sweep", started_at=BASE))
        await db_session.flush()

        run_id = (await client.get("/api/v1/health/jobs")).json()["items"][0]["run_id"]
        assert len(run_id) == 26
        assert "-" not in run_id


class TestSources:
    async def test_lists_and_fetches_a_source(self, client, source):
        listing = (await client.get("/api/v1/sources")).json()
        assert [s["slug"] for s in listing["items"]] == ["wa_sos_filings"]

        detail = (await client.get("/api/v1/sources/wa_sos_filings")).json()
        assert detail["kind"] == "csv"
        assert len(detail["id"]) == 26

    async def test_unknown_slug_is_404(self, client, source):
        assert (await client.get("/api/v1/sources/nope")).status_code == 404

    async def test_limit_above_the_cap_is_rejected_not_clamped(self, client):
        """A clamp would make a short page ambiguous with exhaustion."""
        assert (await client.get("/api/v1/sources?limit=201")).status_code == 422


class TestSourceCoverage:
    async def test_known_source_with_no_rows_is_unaudited_not_empty_coverage(self, client, source):
        """The contract the whole endpoint turns on. ``source_coverage`` is empty in
        production until the next harvest seeds it, so this is the *common* answer:
        it must say "nobody has audited this" rather than imply "covers nothing"."""
        response = await client.get("/api/v1/sources/wa_sos_filings/coverage")
        assert response.status_code == 200
        body = response.json()
        assert body["coverage_recorded"] is False
        assert body["items"] == []
        assert body["known_gaps"] == []
        assert body["dimensions"] == []

    async def test_unknown_source_is_404_not_an_empty_coverage_document(self, client, source):
        """ "No such feed" and "feed never audited" are different facts."""
        assert (await client.get("/api/v1/sources/nope/coverage")).status_code == 404

    async def test_preserves_all_three_statuses_and_surfaces_the_known_gaps(
        self, client, db_session, source
    ):
        rows = [
            ("election_year", "2008", "2018", "verified"),
            ("election_year", "2020", None, "absent"),
            ("sponsor_roster", "1991-92", None, "assumed"),
        ]
        for dimension, start, end, status in rows:
            db_session.add(
                SourceCoverage(
                    source_id=source.id,
                    dimension=dimension,
                    range_start=start,
                    range_end=end,
                    status=status,
                    audited_at=BASE,
                    notes="audited",
                )
            )
        await db_session.flush()

        body = (await client.get("/api/v1/sources/wa_sos_filings/coverage")).json()
        assert body["coverage_recorded"] is True
        assert sorted(i["status"] for i in body["items"]) == ["absent", "assumed", "verified"]
        assert body["dimensions"] == ["election_year", "sponsor_roster"]

        assert [g["range_start"] for g in body["known_gaps"]] == ["2020"]
        assert body["known_gaps"][0]["range_end"] is None

    async def test_an_open_ended_range_serializes_as_null_not_a_sentinel(
        self, client, db_session, source
    ):
        db_session.add(
            SourceCoverage(
                source_id=source.id,
                dimension="election_year",
                range_start="2008",
                range_end=None,
                status="verified",
                audited_at=BASE,
                notes="still serving",
            )
        )
        await db_session.flush()

        body = (await client.get("/api/v1/sources/wa_sos_filings/coverage")).json()
        assert body["items"][0]["range_end"] is None


class TestProvenance:
    @pytest.fixture
    async def cited_entity(self, db_session, source) -> ULID:
        entity_id = _ordered_ulid(5)
        for day in (1, 2):
            event = FetchEvent(
                id=_ordered_ulid(10 + day),
                source_id=source.id,
                resource_id=f"sponsors:2019-20#{day}",
                url=f"https://example.invalid/{day}",
                fetched_at=BASE + timedelta(days=day),
                status=FetchStatus.ok,
                content_hash=bytes([day]) * 32,
            )
            db_session.add(event)
            await db_session.flush()
            db_session.add(
                Citation(
                    id=_ordered_ulid(20 + day),
                    entity_type="assignment",
                    entity_id=entity_id,
                    fetch_event_id=event.id,
                    asserted_at=BASE + timedelta(days=day),
                )
            )
        await db_session.flush()
        return entity_id

    async def test_returns_the_chain_newest_first(self, client, cited_entity):
        body = (await client.get(f"/api/v1/provenance/assignment/{cited_entity}")).json()
        assert [c["resource_id"] for c in body["items"]] == [
            "sponsors:2019-20#2",
            "sponsors:2019-20#1",
        ]

    async def test_flattens_the_source_and_fetch_event_onto_each_citation(
        self, client, cited_entity
    ):
        item = (await client.get(f"/api/v1/provenance/assignment/{cited_entity}")).json()["items"][
            0
        ]
        assert item["source_slug"] == "wa_sos_filings"
        assert item["url"] == "https://example.invalid/2"
        assert item["fetch_status"] == "ok"
        assert item["content_hash"] == (bytes([2]) * 32).hex()

    async def test_an_uncited_entity_is_an_empty_page(self, client, source):
        """No FK on ``entity_id`` by design, so the route cannot tell "no provenance"
        from "no such row" — and does not pretend to."""
        body = (await client.get(f"/api/v1/provenance/person/{ULID()}")).json()
        assert body["items"] == []

    async def test_a_uuid_hex_entity_id_is_rejected(self, client):
        """The ``::text``-cast trap, at the request boundary."""
        response = await client.get(f"/api/v1/provenance/person/{ULID().to_uuid()}")
        assert response.status_code == 422

    async def test_a_malformed_cursor_is_422_not_500(self, client, cited_entity):
        response = await client.get(
            f"/api/v1/provenance/assignment/{cited_entity}?cursor=not-a-ulid"
        )
        assert response.status_code == 422
