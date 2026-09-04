"""Behaviour of the ``/api/v1`` products slice against the serving schema (#313).

The successor to ``test_v1_canonical.py``. What changed is not only where the
rows come from but what an identity *is*, so these pin the three places that
carries risk:

* **Keyset pagination**, now over string keys and — for assignments — over the
  five columns that are a span's identity. A cursor that skipped or repeated a
  row would be invisible in a small fixture, so pages are asserted
  element-by-element.
* **The span key at the HTTP boundary.** An assignment has no ULID any more; it
  is addressed by ``{member}:{kind}:{disc}:{start}``, and the roster family's
  member ids carry their own colon.
* **The provenance chain**, which now resolves through the published citations
  artifact to a digest rather than through a fetch-event ledger.
"""

from datetime import date

import pytest
from sqlalchemy import insert

from usa_wa_api.serving.schema import (
    Assignment,
    Citation,
    Organization,
    Person,
    PersonCrosswalk,
    RawFetch,
    Role,
)

pytestmark = pytest.mark.db

WSL = "usa_wa_legislature"
ROSTER = "usa_wa_legislature_roster"

PERSON_A = "01JAAAAAAAAAAAAAAAAAAAAAAA"
PERSON_B = "01JBBBBBBBBBBBBBBBBBBBBBBB"
PERSON_C = "01JCCCCCCCCCCCCCCCCCCCCCCC"
ORG_A = "01JDDDDDDDDDDDDDDDDDDDDDDD"
ORG_B = "01JEEEEEEEEEEEEEEEEEEEEEEE"
ROLE_A = "01JFFFFFFFFFFFFFFFFFFFFFFF"
ROLE_B = "01JGGGGGGGGGGGGGGGGGGGGGGG"


async def _insert(session, table, rows: list[dict]) -> None:
    if rows:
        await session.execute(insert(table.__table__), rows)


@pytest.fixture
async def corpus(serving_schema):
    """A small but structurally complete serving snapshot.

    Two identity spaces, two organizations, two roles, four spans — enough that
    every filter can be shown to *exclude* something, which a single-row fixture
    can never demonstrate.
    """
    session = serving_schema
    await _insert(
        session,
        Person,
        [
            {"entity_id": PERSON_A, "name_full": "Dana Whitfield", "name_source": "roster"},
            {"entity_id": PERSON_B, "name_full": "Sam Ortega", "name_source": "wsl"},
            {"entity_id": PERSON_C, "name_full": "Lee Whitfield", "name_source": "pdc"},
        ],
    )
    await _insert(
        session,
        PersonCrosswalk,
        [
            {
                "natural_key": f"{WSL}:100",
                "entity_id": PERSON_A,
                "key_namespace": WSL,
                "key_value": "100",
                "registered_by": "seed",
                "merged_into": None,
            },
            {
                "natural_key": "wa_pdc:7710",
                "entity_id": PERSON_A,
                "key_namespace": "wa_pdc",
                "key_value": "7710",
                "registered_by": "registrar",
                "merged_into": None,
            },
            {
                "natural_key": f"{WSL}:200",
                "entity_id": PERSON_B,
                "key_namespace": WSL,
                "key_value": "200",
                "registered_by": "seed",
                "merged_into": None,
            },
        ],
    )
    await _insert(
        session,
        Organization,
        [
            {
                "entity_id": ORG_A,
                "name": "Ag",
                "long_name": "Agriculture",
                "agency": "House",
                "org_type": "committee",
                "first_biennium": "2019-20",
                "last_biennium": "2025-26",
            },
            {
                "entity_id": ORG_B,
                "name": "Rules",
                "long_name": "Rules",
                "agency": "Senate",
                "org_type": "other",
                "first_biennium": "2021-22",
                "last_biennium": "2025-26",
            },
        ],
    )
    await _insert(
        session,
        Role,
        [
            {
                "role_key": "committee-member-role:5",
                "entity_id": ROLE_A,
                "role_type": "committee_member",
                "name": "Ag Member",
                "span_kind": "committee",
                "span_discriminator": "5",
                "org_source_id": "5",
                "org_entity_id": ORG_A,
                "district": None,
                "qualifier": None,
            },
            {
                "role_key": "senate-seat-role:14",
                "entity_id": ROLE_B,
                "role_type": "state_senator",
                "name": "LD 14 Senator",
                "span_kind": "chamber-senate",
                "span_discriminator": "14",
                "org_source_id": "usa_wa_senate",
                "org_entity_id": ORG_B,
                "district": 14,
                "qualifier": None,
            },
        ],
    )
    await _insert(
        session,
        Assignment,
        [
            {
                "source": WSL,
                "member_id": "100",
                "span_kind": "committee",
                "span_discriminator": "5",
                "span_start_biennium": "2019-20",
                "entity_id": PERSON_A,
                "role_key": "committee-member-role:5",
                "span_end_biennium": "2021-22",
                "valid_from": date(2019, 1, 14),
                "valid_to": date(2022, 12, 31),
                "is_active": False,
            },
            {
                "source": WSL,
                "member_id": "100",
                "span_kind": "chamber-senate",
                "span_discriminator": "14",
                "span_start_biennium": "2023-24",
                "entity_id": PERSON_A,
                "role_key": "senate-seat-role:14",
                "span_end_biennium": None,
                "valid_from": date(2023, 1, 9),
                "valid_to": None,
                "is_active": True,
            },
            {
                "source": WSL,
                "member_id": "200",
                "span_kind": "committee",
                "span_discriminator": "5",
                "span_start_biennium": "2021-22",
                "entity_id": PERSON_B,
                "role_key": "committee-member-role:5",
                "span_end_biennium": "2021-22",
                "valid_from": date(2021, 1, 11),
                "valid_to": date(2022, 12, 31),
                "is_active": False,
            },
            {
                # The roster family: a member id that carries its own colon.
                "source": ROSTER,
                "member_id": "jsmith:1937",
                "span_kind": "chamber-senate",
                "span_discriminator": "28",
                "span_start_biennium": "1937-38",
                "entity_id": PERSON_C,
                "role_key": "senate-seat-role:28",
                "span_end_biennium": "1939-40",
                "valid_from": date(1937, 1, 11),
                "valid_to": date(1940, 12, 31),
                "is_active": False,
            },
        ],
    )
    await session.flush()
    return session


class TestPersons:
    async def test_lists_every_live_entity(self, client, corpus) -> None:
        body = (await client.get("/api/v1/persons")).json()
        assert [item["entity_id"] for item in body["items"]] == [PERSON_A, PERSON_B, PERSON_C]
        assert body["items"][0]["name_source"] == "roster"

    async def test_source_filters_on_what_the_registry_knows_not_on_origin(
        self, client, corpus
    ) -> None:
        """A person is multi-source by construction now: PERSON_A is reachable
        by BOTH its WSL key and its PDC one, which the old `source` scalar could
        not express at all."""
        pdc = (await client.get("/api/v1/persons", params={"source": "wa_pdc"})).json()
        assert [item["entity_id"] for item in pdc["items"]] == [PERSON_A]
        wsl = (await client.get("/api/v1/persons", params={"source": WSL})).json()
        assert [item["entity_id"] for item in wsl["items"]] == [PERSON_A, PERSON_B]

    async def test_name_substring_is_case_insensitive(self, client, corpus) -> None:
        body = (await client.get("/api/v1/persons", params={"name_contains": "whitfield"})).json()
        assert [item["entity_id"] for item in body["items"]] == [PERSON_A, PERSON_C]

    async def test_pages_without_skipping_or_repeating(self, client, corpus) -> None:
        first = (await client.get("/api/v1/persons", params={"limit": 2})).json()
        assert [item["entity_id"] for item in first["items"]] == [PERSON_A, PERSON_B]
        assert first["next_cursor"] == PERSON_B
        second = (
            await client.get("/api/v1/persons", params={"limit": 2, "cursor": first["next_cursor"]})
        ).json()
        assert [item["entity_id"] for item in second["items"]] == [PERSON_C]
        assert second["next_cursor"] is None

    async def test_detail_carries_the_crosswalk_not_an_identifier_table(
        self, client, corpus
    ) -> None:
        """`PersonIdentifier` is gone: identity is the registry's, so an external
        id is a KEY bound to an entity rather than a row hanging off a person."""
        body = (await client.get(f"/api/v1/persons/{PERSON_A}")).json()
        assert [key["natural_key"] for key in body["identifiers"]] == [
            f"{WSL}:100",
            "wa_pdc:7710",
        ]
        assert body["identifiers"][1]["key_namespace"] == "wa_pdc"
        assert body["identifiers"][1]["merged_into"] is None

    async def test_unknown_id_is_404(self, client, corpus) -> None:
        assert (await client.get("/api/v1/persons/01JZZZZZZZZZZZZZZZZZZZZZZZ")).status_code == 404

    async def test_a_uuid_hex_id_is_422_not_404(self, client, corpus) -> None:
        """A consumer that round-tripped an id through a `::text` cast gets told,
        rather than a 404 they will read as "no such person"."""
        response = await client.get(
            "/api/v1/persons/0192d4a1-0000-7000-8000-000000000000",
        )
        assert response.status_code == 422


class TestOrganizations:
    async def test_filters_by_org_type_and_agency(self, client, corpus) -> None:
        body = (await client.get("/api/v1/organizations", params={"org_type": "other"})).json()
        assert [item["entity_id"] for item in body["items"]] == [ORG_B]
        body = (await client.get("/api/v1/organizations", params={"agency": "House"})).json()
        assert [item["entity_id"] for item in body["items"]] == [ORG_A]

    async def test_carries_the_biennium_bounds(self, client, corpus) -> None:
        body = (await client.get(f"/api/v1/organizations/{ORG_A}")).json()
        assert (body["first_biennium"], body["last_biennium"]) == ("2019-20", "2025-26")
        assert body["long_name"] == "Agriculture"


class TestRoles:
    async def test_orders_by_the_structural_key(self, client, corpus) -> None:
        """A role minted later still sorts beside its siblings — which is what
        makes paging through a committee's seats coherent."""
        body = (await client.get("/api/v1/roles")).json()
        assert [item["role_key"] for item in body["items"]] == [
            "committee-member-role:5",
            "senate-seat-role:14",
        ]
        assert body["items"][0]["entity_id"] == ROLE_A

    async def test_filters_by_organization_role_type_and_district(self, client, corpus) -> None:
        by_org = (await client.get("/api/v1/roles", params={"organization_id": ORG_B})).json()
        assert [item["role_key"] for item in by_org["items"]] == ["senate-seat-role:14"]
        by_type = (
            await client.get("/api/v1/roles", params={"role_type": "committee_member"})
        ).json()
        assert [item["role_key"] for item in by_type["items"]] == ["committee-member-role:5"]
        by_district = (await client.get("/api/v1/roles", params={"district": 14})).json()
        assert [item["role_key"] for item in by_district["items"]] == ["senate-seat-role:14"]

    async def test_addressed_by_the_registry_ulid_not_the_derived_key(self, client, corpus) -> None:
        """The key is derived, so it moves when the derivation is corrected, and
        an id that moves is not an id. Both are on the response either way."""
        body = (await client.get(f"/api/v1/roles/{ROLE_B}")).json()
        assert body["role_key"] == "senate-seat-role:14"
        assert body["district"] == 14


class TestAssignments:
    async def test_the_span_key_is_the_id_and_is_built_from_columns(self, client, corpus) -> None:
        body = (await client.get("/api/v1/assignments", params={"person_id": PERSON_B})).json()
        [item] = body["items"]
        assert item["assignment_id"] == "200:committee:5:2021-22"
        assert item["span_kind"] == "committee"
        assert item["span_discriminator"] == "5"
        assert item["span_start_biennium"] == "2021-22"

    async def test_span_kind_filters_a_column_not_a_string_split(self, client, corpus) -> None:
        """#335: the old filter split `source_id` left-to-right and under-reported
        by 83%, because the roster family's member ids carry their own colon.
        Both families answer here."""
        body = (
            await client.get("/api/v1/assignments", params={"span_kind": "chamber-senate"})
        ).json()
        assert {item["assignment_id"] for item in body["items"]} == {
            "100:chamber-senate:14:2023-24",
            "jsmith:1937:chamber-senate:28:1937-38",
        }

    async def test_filters_by_role_ulid_and_by_role_key(self, client, corpus) -> None:
        by_id = (await client.get("/api/v1/assignments", params={"role_id": ROLE_A})).json()
        by_key = (
            await client.get("/api/v1/assignments", params={"role_key": "committee-member-role:5"})
        ).json()
        assert {item["assignment_id"] for item in by_id["items"]} == {
            "100:committee:5:2019-20",
            "200:committee:5:2021-22",
        }
        assert [item["assignment_id"] for item in by_id["items"]] == [
            item["assignment_id"] for item in by_key["items"]
        ]

    async def test_as_of_matches_open_and_closed_spans(self, client, corpus) -> None:
        body = (await client.get("/api/v1/assignments", params={"as_of": "2021-06-01"})).json()
        assert {item["assignment_id"] for item in body["items"]} == {
            "100:committee:5:2019-20",
            "200:committee:5:2021-22",
        }
        open_only = (await client.get("/api/v1/assignments", params={"as_of": "2026-01-01"})).json()
        assert [item["assignment_id"] for item in open_only["items"]] == [
            "100:chamber-senate:14:2023-24"
        ]

    async def test_is_active_selects_the_open_span(self, client, corpus) -> None:
        body = (await client.get("/api/v1/assignments", params={"is_active": True})).json()
        assert [item["assignment_id"] for item in body["items"]] == [
            "100:chamber-senate:14:2023-24"
        ]
        assert body["items"][0]["valid_to"] is None

    async def test_pages_over_the_composite_key(self, client, corpus) -> None:
        """The cursor carries five columns, so a page boundary that ties on an
        earlier one still resumes in exactly the right place."""
        seen: list[str] = []
        cursor = None
        for _ in range(4):
            params = {"limit": 1} | ({"cursor": cursor} if cursor else {})
            body = (await client.get("/api/v1/assignments", params=params)).json()
            seen += [item["assignment_id"] for item in body["items"]]
            cursor = body["next_cursor"]
            if cursor is None:
                break
        assert cursor is None
        assert seen == [
            "100:chamber-senate:14:2023-24",
            "100:committee:5:2019-20",
            "200:committee:5:2021-22",
            "jsmith:1937:chamber-senate:28:1937-38",
        ]
        assert len(seen) == len(set(seen))

    async def test_a_cursor_from_another_route_is_refused(self, client, corpus) -> None:
        """It decodes cleanly and means something else; using it would resume the
        scan at an arbitrary point rather than failing."""
        response = await client.get("/api/v1/assignments", params={"cursor": PERSON_A})
        assert response.status_code == 422

    async def test_a_roster_id_is_split_from_the_right(self, client, corpus) -> None:
        """#259: the member id is `<fold>:<year>`, so the key has five segments
        and a left-to-right split would address the wrong span — or none."""
        body = (
            await client.get("/api/v1/assignments/jsmith:1937:chamber-senate:28:1937-38")
        ).json()
        assert body["member_id"] == "jsmith:1937"
        assert body["source"] == ROSTER
        assert body["span_discriminator"] == "28"

    async def test_a_malformed_id_is_422_not_404(self, client, corpus) -> None:
        assert (await client.get("/api/v1/assignments/nonsense")).status_code == 422

    async def test_unknown_id_is_404(self, client, corpus) -> None:
        assert (
            await client.get("/api/v1/assignments/999:party:democratic:2019-20")
        ).status_code == 404

    async def test_detail_carries_the_citation_chain_with_its_digest(self, client, corpus) -> None:
        session = corpus
        await _insert(
            session,
            RawFetch,
            [
                {
                    "source": WSL,
                    "resource_id": "sponsors:2023-24",
                    "sha256": "abc123",
                    "fetched_at": "2026-09-04T08:03:56.172554Z",
                    "run_id": "r1",
                    "url": "https://wslwebservices.leg.wa.gov/",
                    "bytes": 7503,
                    "content_type": "text/xml",
                }
            ],
        )
        await _insert(
            session,
            Citation,
            [
                {
                    "entity_type": "assignment",
                    "entity_id": "100:chamber-senate:14:2023-24",
                    "source": WSL,
                    "resource_id": "sponsors:2023-24",
                }
            ],
        )
        await session.flush()
        body = (await client.get("/api/v1/assignments/100:chamber-senate:14:2023-24")).json()
        [citation] = body["citations"]
        assert citation["sha256"] == "abc123"
        assert citation["url"] == "https://wslwebservices.leg.wa.gov/"
        assert citation["resource_id"] == "sponsors:2023-24"

    async def test_a_citation_with_no_attestation_still_appears(self, client, corpus) -> None:
        """The join is a LEFT one. An orphan citation is an integrity break the
        nightly probe gates at zero, and hiding it here would deny a reader the
        very thing that probe exists to shout about."""
        session = corpus
        await _insert(
            session,
            Citation,
            [
                {
                    "entity_type": "assignment",
                    "entity_id": "100:chamber-senate:14:2023-24",
                    "source": WSL,
                    "resource_id": "vanished:2023-24",
                }
            ],
        )
        await session.flush()
        body = (await client.get("/api/v1/assignments/100:chamber-senate:14:2023-24")).json()
        [citation] = body["citations"]
        assert citation["resource_id"] == "vanished:2023-24"
        assert citation["sha256"] is None

    async def test_detail_of_an_uncited_span_carries_an_empty_chain(self, client, corpus) -> None:
        body = (await client.get("/api/v1/assignments/200:committee:5:2021-22")).json()
        assert body["citations"] == []
