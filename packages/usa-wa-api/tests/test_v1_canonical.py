"""Behaviour of the ``/api/v1`` canonical slice (#184) against a real database.

Three things carry most of the risk and get most of the tests:

* **Keyset pagination** — a cursor that skipped or repeated a row would be
  invisible in a small fixture, so the pages are asserted element-by-element.
* **Liveness across joins** — ``live_only`` must be applied once per lifecycle
  model the query joins through. A live Role under an *archived* Organization is
  the case that silently leaks when it is not.
* **The span key** — a tenure span is an Assignment and its kind lives in the
  ``source_id``, so the parse has to be right at the HTTP boundary, not just in
  the model.
"""

from datetime import UTC, date, datetime, timedelta

import pytest
from ulid import ULID

from clearinghouse_core.provenance import Citation, FetchEvent, FetchStatus, Source
from clearinghouse_domain_legislative.identity import (
    Assignment,
    Organization,
    Person,
    PersonIdentifier,
    Role,
)

BASE = datetime(2026, 1, 1, tzinfo=UTC)


def _ordered_ulid(offset: int) -> ULID:
    return ULID.from_datetime(BASE + timedelta(days=offset))


@pytest.fixture
async def chamber(db_session, usa_wa) -> Organization:
    row = Organization(
        id=_ordered_ulid(1),
        jurisdiction_id=usa_wa.id,
        source="usa_wa_legislature",
        source_id="chamber-house",
        name="Washington State House of Representatives",
        org_type="chamber",
    )
    db_session.add(row)
    await db_session.flush()
    return row


@pytest.fixture
async def seat(db_session, chamber, usa_wa) -> Role:
    row = Role(
        id=_ordered_ulid(2),
        source="usa_wa_legislature",
        source_id="seat-ld5-p1",
        organization_id=chamber.id,
        name="LD-5 Position 1",
        role_type="elected_member",
        jurisdiction_id=usa_wa.id,
        qualifier="Position 1",
    )
    db_session.add(row)
    await db_session.flush()
    return row


async def _person(db_session, *, offset: int, name: str, **kwargs) -> Person:
    row = Person(
        id=_ordered_ulid(offset),
        source="usa_wa_legislature",
        source_id=f"member-{offset}",
        name_full=name,
        **kwargs,
    )
    db_session.add(row)
    await db_session.flush()
    return row


class TestPersons:
    async def test_pages_without_skipping_or_repeating(self, client, db_session):
        for offset, name in ((10, "Adams, A"), (11, "Baker, B"), (12, "Chen, C")):
            await _person(db_session, offset=offset, name=name)

        first = (await client.get("/api/v1/persons?limit=2")).json()
        assert [p["name_full"] for p in first["items"]] == ["Adams, A", "Baker, B"]
        assert first["next_cursor"] == first["items"][-1]["id"]

        second = (await client.get(f"/api/v1/persons?limit=2&cursor={first['next_cursor']}")).json()
        assert [p["name_full"] for p in second["items"]] == ["Chen, C"]
        assert second["next_cursor"] is None

    async def test_a_full_final_page_still_reports_exhaustion(self, client, db_session):
        """``limit`` rows with no overflow row means done — a cursor here would
        hand the caller an empty page."""
        await _person(db_session, offset=10, name="Adams, A")
        await _person(db_session, offset=11, name="Baker, B")

        body = (await client.get("/api/v1/persons?limit=2")).json()
        assert len(body["items"]) == 2
        assert body["next_cursor"] is None

    async def test_archived_and_deleted_are_hidden_by_default(self, client, db_session):
        await _person(db_session, offset=10, name="Live, L")
        await _person(db_session, offset=11, name="Archived, A", archived_at=BASE)
        await _person(db_session, offset=12, name="Deleted, D", deleted_at=BASE)

        body = (await client.get("/api/v1/persons")).json()
        assert [p["name_full"] for p in body["items"]] == ["Live, L"]

    async def test_include_hidden_is_the_explicit_audit_escape_hatch(self, client, db_session):
        await _person(db_session, offset=10, name="Live, L")
        await _person(db_session, offset=11, name="Archived, A", archived_at=BASE)

        body = (await client.get("/api/v1/persons?include_hidden=true")).json()
        assert [p["name_full"] for p in body["items"]] == ["Live, L", "Archived, A"]
        assert body["items"][1]["archived_at"] is not None

    async def test_name_substring_filter_is_case_insensitive(self, client, db_session):
        await _person(db_session, offset=10, name="Ormsby, Timm")
        await _person(db_session, offset=11, name="Chopp, Frank")

        body = (await client.get("/api/v1/persons?name_contains=ormsby")).json()
        assert [p["name_full"] for p in body["items"]] == ["Ormsby, Timm"]

    async def test_detail_carries_the_identifier_graph(self, client, db_session):
        person = await _person(db_session, offset=10, name="Ormsby, Timm")
        db_session.add(
            PersonIdentifier(
                source="usa_wa_legislature",
                source_id="wsl-1",
                person_id=person.id,
                scheme="wsl_member_id",
                value="12345",
            )
        )
        await db_session.flush()

        body = (await client.get(f"/api/v1/persons/{person.id}")).json()
        assert body["identifiers"] == [
            {
                "id": body["identifiers"][0]["id"],
                "scheme": "wsl_member_id",
                "value": "12345",
                "source": "usa_wa_legislature",
                "source_id": "wsl-1",
            }
        ]

    async def test_detail_returns_an_archived_row_rather_than_404ing(self, client, db_session):
        """A caller holding an id is usually asking *because* the row went quiet."""
        person = await _person(db_session, offset=10, name="Gone, G", archived_at=BASE)
        body = (await client.get(f"/api/v1/persons/{person.id}")).json()
        assert body["archived_at"] is not None

    async def test_unknown_id_is_404(self, client):
        assert (await client.get(f"/api/v1/persons/{ULID()}")).status_code == 404

    async def test_uuid_hex_id_is_422_not_404(self, client):
        """A 404 would read as "no such person" and send the caller hunting."""
        assert (await client.get(f"/api/v1/persons/{ULID().to_uuid()}")).status_code == 422


class TestOrganizations:
    async def test_active_is_a_filter_and_not_a_liveness_axis(self, client, db_session, usa_wa):
        """A dissolved committee is inactive, not archived — it stays in every read
        unless the caller asks otherwise (``docs/ONTOLOGY.md`` § Lifecycle axes)."""
        db_session.add(
            Organization(
                id=_ordered_ulid(20),
                jurisdiction_id=usa_wa.id,
                source="usa_wa_legislature",
                source_id="cmte-dissolved",
                name="Defunct Committee",
                org_type="committee",
                active=False,
            )
        )
        await db_session.flush()

        unfiltered = (await client.get("/api/v1/organizations")).json()
        assert [o["name"] for o in unfiltered["items"]] == ["Defunct Committee"]

        filtered = (await client.get("/api/v1/organizations?active=false")).json()
        assert [o["name"] for o in filtered["items"]] == ["Defunct Committee"]
        assert (await client.get("/api/v1/organizations?active=true")).json()["items"] == []

    async def test_filters_by_org_type(self, client, db_session, chamber, usa_wa):
        db_session.add(
            Organization(
                id=_ordered_ulid(21),
                jurisdiction_id=usa_wa.id,
                source="usa_wa_legislature",
                source_id="party-d",
                name="Democratic Party",
                org_type="party",
            )
        )
        await db_session.flush()

        body = (await client.get("/api/v1/organizations?org_type=party")).json()
        assert [o["org_type"] for o in body["items"]] == ["party"]

    async def test_a_malformed_jurisdiction_filter_names_the_field(self, client):
        response = await client.get("/api/v1/organizations?jurisdiction_id=nope")
        assert response.status_code == 422
        assert "jurisdiction_id" in response.json()["detail"]


class TestRoles:
    async def test_an_archived_organization_hides_its_roles(
        self, client, db_session, chamber, seat
    ):
        """The join hop `live_only`'s docstring warns about: a live Role under an
        archived Organization leaks unless the org is filtered too."""
        assert len((await client.get("/api/v1/roles")).json()["items"]) == 1

        chamber.archived_at = BASE
        await db_session.flush()

        assert (await client.get("/api/v1/roles")).json()["items"] == []
        assert len((await client.get("/api/v1/roles?include_hidden=true")).json()["items"]) == 1

    async def test_seat_geography_round_trips(self, client, seat, usa_wa):
        body = (await client.get(f"/api/v1/roles/{seat.id}")).json()
        assert body["qualifier"] == "Position 1"
        assert body["jurisdiction_id"] == str(usa_wa.id)


class TestAssignments:
    async def _span(self, db_session, *, offset: int, source_id: str, role: Role, **kwargs):
        row = Assignment(
            id=_ordered_ulid(offset),
            source="usa_wa_legislature",
            source_id=source_id,
            holder_name_raw="Ormsby, Timm",
            role_id=role.id,
            valid_from=kwargs.pop("valid_from", date(2019, 1, 1)),
            **kwargs,
        )
        db_session.add(row)
        await db_session.flush()
        return row

    async def test_parses_the_span_key_over_http(self, client, db_session, seat):
        await self._span(
            db_session,
            offset=30,
            source_id="12345:chamber-house:ld-5-position-1:2019-20",
            role=seat,
        )
        item = (await client.get("/api/v1/assignments")).json()["items"][0]
        assert item["span_kind"] == "chamber-house"
        assert item["span_discriminator"] == "ld-5-position-1"
        assert item["span_start_biennium"] == "2019-20"

    async def test_a_legacy_source_id_yields_null_span_fields(self, client, db_session, seat):
        await self._span(db_session, offset=30, source_id="12345-legacy", role=seat)
        item = (await client.get("/api/v1/assignments")).json()["items"][0]
        assert item["span_kind"] is None

    async def test_span_kind_filter_ignores_non_span_source_ids(self, client, db_session, seat):
        """A 2-part legacy id has *something* in position 2; matching it would report
        a span kind the row does not carry."""
        await self._span(
            db_session, offset=30, source_id="12345:chamber-house:ld-5:2019-20", role=seat
        )
        await self._span(db_session, offset=31, source_id="9:chamber-house", role=seat)

        body = (await client.get("/api/v1/assignments?span_kind=chamber-house")).json()
        assert [a["source_id"] for a in body["items"]] == ["12345:chamber-house:ld-5:2019-20"]

    async def test_as_of_matches_open_and_closed_spans(self, client, db_session, seat):
        await self._span(
            db_session,
            offset=30,
            source_id="1:chamber-house:ld-5:2011-12",
            role=seat,
            valid_from=date(2011, 1, 1),
            valid_to=date(2012, 12, 31),
        )
        await self._span(
            db_session,
            offset=31,
            source_id="2:chamber-house:ld-5:2019-20",
            role=seat,
            valid_from=date(2019, 1, 1),
            valid_to=None,
            is_active=True,
        )

        closed = (await client.get("/api/v1/assignments?as_of=2011-06-01")).json()
        assert [a["source_id"] for a in closed["items"]] == ["1:chamber-house:ld-5:2011-12"]

        open_now = (await client.get("/api/v1/assignments?as_of=2030-06-01")).json()
        assert [a["source_id"] for a in open_now["items"]] == ["2:chamber-house:ld-5:2019-20"]

    async def test_an_archived_organization_hides_the_tenures_under_it(
        self, client, db_session, chamber, seat
    ):
        await self._span(db_session, offset=30, source_id="1:chamber-house:ld-5:2019-20", role=seat)
        assert len((await client.get("/api/v1/assignments")).json()["items"]) == 1

        chamber.archived_at = BASE
        await db_session.flush()
        assert (await client.get("/api/v1/assignments")).json()["items"] == []

    async def test_detail_carries_the_citation_chain(self, client, db_session, seat, usa_wa):
        span = await self._span(
            db_session, offset=30, source_id="1:chamber-house:ld-5:2019-20", role=seat
        )
        source = Source(
            id=_ordered_ulid(40),
            jurisdiction_id=usa_wa.id,
            name="WSL",
            slug="wa_legislature",
            kind="soap",
        )
        db_session.add(source)
        await db_session.flush()
        event = FetchEvent(
            id=_ordered_ulid(41),
            source_id=source.id,
            resource_id="sponsors:2019-20",
            url="https://example.invalid/sponsors",
            fetched_at=BASE,
            status=FetchStatus.ok,
        )
        db_session.add(event)
        await db_session.flush()
        db_session.add(
            Citation(
                entity_type="assignment",
                entity_id=span.id,
                fetch_event_id=event.id,
                asserted_at=BASE,
            )
        )
        await db_session.flush()

        body = (await client.get(f"/api/v1/assignments/{span.id}")).json()
        assert [c["resource_id"] for c in body["citations"]] == ["sponsors:2019-20"]
        assert body["citations"][0]["source_slug"] == "wa_legislature"
        assert body["citations"][0]["content_hash"] is None

    async def test_detail_of_an_uncited_span_carries_an_empty_chain(self, client, db_session, seat):
        span = await self._span(
            db_session, offset=30, source_id="1:chamber-house:ld-5:2019-20", role=seat
        )
        assert (await client.get(f"/api/v1/assignments/{span.id}")).json()["citations"] == []
