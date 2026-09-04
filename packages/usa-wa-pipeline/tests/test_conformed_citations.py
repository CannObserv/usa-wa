"""The citations artifact (#313): entity → the raw resources that attest it.

The replacement for the Postgres ``Citation`` chain, computed as a stateless
join instead of written as a ledger. What the incumbent did per biennium at
emit time (``span_emit._ensure_citations``), this does per biennium at build
time — the same rule, from staging rows that now remember their own wire.
"""

import pytest

from usa_wa_pipeline.conformed import citations
from usa_wa_pipeline.conformed.citations import CitationInputs, citation_rows

WSL = "usa_wa_legislature"
ROSTER_SOURCE = "usa_wa_legislature_roster"
PDC = "usa_wa_pdc"

PERSON_A = "01JAAAAAAAAAAAAAAAAAAAAAAA"
PERSON_B = "01JBBBBBBBBBBBBBBBBBBBBBBB"
ORG_A = "01JCCCCCCCCCCCCCCCCCCCCCCC"
ROLE_A = "01JDDDDDDDDDDDDDDDDDDDDDDD"


def _key(natural_key: str, entity_id: str, merged_into: str | None = None) -> dict:
    namespace, _, value = natural_key.partition(":")
    return {
        "entity_id": entity_id,
        "natural_key": natural_key,
        "key_namespace": namespace,
        "key_value": value,
        "registered_by": "registrar",
        "merged_into": merged_into,
    }


def _sponsor(member_id: str, biennium: str, resource: str | None = None) -> dict:
    return {
        "member_id": member_id,
        "biennium": biennium,
        "name": f"M{member_id}",
        "source": WSL,
        "resource_id": resource or f"sponsors:{biennium}",
    }


def _committee_member(member_id: str, committee_id: str, biennium: str) -> dict:
    return {
        "member_id": member_id,
        "committee_id": committee_id,
        "biennium": biennium,
        "source": WSL,
        "resource_id": f"committee-members-hist:{biennium}:{committee_id}:House:Ag",
    }


def _assignment(**over) -> dict:
    row = {
        "entity_id": PERSON_A,
        "member_id": "100",
        "source": WSL,
        "role_key": "party-role:D",
        "span_kind": "party",
        "span_discriminator": "D",
        "span_start_biennium": "2019-20",
        "span_end_biennium": "2019-20",
        "valid_from": "2019-01-14",
        "valid_to": "2020-12-31",
        "is_active": False,
    }
    row.update(over)
    return row


def _inputs(**over) -> CitationInputs:
    base = {
        "person_crosswalk": [],
        "org_crosswalk": [],
        "roles": [],
        "assignments": [],
        "sponsors": [],
        "committee_members": [],
        "committees": [],
        "meetings": [],
        "pdc": [],
        "roster": [],
    }
    base.update(over)
    return CitationInputs(**base)


def _cited(rows, entity_type, entity_id) -> set[tuple[str, str]]:
    return {
        (r["source"], r["resource_id"])
        for r in rows
        if r["entity_type"] == entity_type and r["entity_id"] == entity_id
    }


class TestPersons:
    def test_every_source_that_names_a_person_cites_it(self) -> None:
        rows, _ = citation_rows(
            _inputs(
                person_crosswalk=[
                    _key(f"{WSL}:100", PERSON_A),
                    _key("wa_pdc:p9", PERSON_A),
                ],
                sponsors=[_sponsor("100", "2019-20")],
                pdc=[
                    {
                        "person_id": "p9",
                        "source": PDC,
                        "resource_id": "house-winners:2018",
                    }
                ],
            )
        )
        assert _cited(rows, "person", PERSON_A) == {
            (WSL, "sponsors:2019-20"),
            (PDC, "house-winners:2018"),
        }

    def test_a_person_cited_by_two_wires_of_one_source_gets_both(self) -> None:
        rows, _ = citation_rows(
            _inputs(
                person_crosswalk=[_key(f"{WSL}:100", PERSON_A)],
                sponsors=[_sponsor("100", "2019-20"), _sponsor("100", "2021-22")],
            )
        )
        assert _cited(rows, "person", PERSON_A) == {
            (WSL, "sponsors:2019-20"),
            (WSL, "sponsors:2021-22"),
        }

    def test_a_merged_key_cites_the_survivor(self) -> None:
        """The tombstone is the only re-point signal a consumer gets, and a
        citation pointing at a retired entity is a dangling one."""
        rows, _ = citation_rows(
            _inputs(
                person_crosswalk=[_key(f"{WSL}:100", PERSON_B, merged_into=PERSON_A)],
                sponsors=[_sponsor("100", "2019-20")],
            )
        )
        assert _cited(rows, "person", PERSON_A) == {(WSL, "sponsors:2019-20")}
        assert _cited(rows, "person", PERSON_B) == set()

    def test_a_roster_row_cites_through_its_fold(self) -> None:
        """Roster natural keys are ``<fold>:<first-session-year>``; the row
        carries only a name, so the fold half is the join."""
        rows, _ = citation_rows(
            _inputs(
                person_crosswalk=[_key(f"{ROSTER_SOURCE}:jsmith:1937", PERSON_A)],
                roster=[
                    {
                        "name": "J. Smith",
                        "year": 1937,
                        "source": ROSTER_SOURCE,
                        "resource_id": "legroster:2025-06-05",
                    }
                ],
            )
        )
        assert _cited(rows, "person", PERSON_A) == {(ROSTER_SOURCE, "legroster:2025-06-05")}

    def test_an_ambiguous_fold_is_counted_not_guessed(self) -> None:
        """Two entities under one fold is the Jr/Sr signature. Citing either
        would attribute a life to the wrong person."""
        rows, counters = citation_rows(
            _inputs(
                person_crosswalk=[
                    _key(f"{ROSTER_SOURCE}:jsmith:1937", PERSON_A),
                    _key(f"{ROSTER_SOURCE}:jsmith:1975", PERSON_B),
                ],
                roster=[
                    {
                        "name": "J. Smith",
                        "year": 1937,
                        "source": ROSTER_SOURCE,
                        "resource_id": "legroster:2025-06-05",
                    }
                ],
            )
        )
        assert _cited(rows, "person", PERSON_A) == set()
        assert counters["ambiguous_roster_folds"] == 1

    def test_a_key_no_staging_row_names_leaves_the_person_uncited(self) -> None:
        _, counters = citation_rows(
            _inputs(person_crosswalk=[_key(f"{WSL}:100", PERSON_A)], sponsors=[])
        )
        assert counters["uncited_persons"] == 1


class TestOrganizations:
    def test_committee_and_meeting_wires_both_cite_the_org(self) -> None:
        rows, _ = citation_rows(
            _inputs(
                org_crosswalk=[_key(f"{WSL}:5", ORG_A)],
                committees=[
                    {
                        "committee_id": "5",
                        "biennium": "2025-26",
                        "source": WSL,
                        "resource_id": "committees-roster:2025-26",
                    }
                ],
                meetings=[
                    {
                        "committee_id": "5",
                        "source": WSL,
                        "resource_id": "committee-meetings:2025-01-01:2026-12-31",
                    }
                ],
            )
        )
        assert _cited(rows, "organization", ORG_A) == {
            (WSL, "committees-roster:2025-26"),
            (WSL, "committee-meetings:2025-01-01:2026-12-31"),
        }

    def test_a_committee_roster_wire_cites_the_org_it_rosters(self) -> None:
        rows, _ = citation_rows(
            _inputs(
                org_crosswalk=[_key(f"{WSL}:5", ORG_A)],
                committee_members=[_committee_member("100", "5", "2019-20")],
            )
        )
        assert _cited(rows, "organization", ORG_A) == {
            (WSL, "committee-members-hist:2019-20:5:House:Ag")
        }


class TestAssignments:
    def test_a_sponsor_span_is_cited_once_per_covered_biennium(self) -> None:
        """The incumbent rule, unchanged: one citation per biennium in range."""
        rows, _ = citation_rows(
            _inputs(
                assignments=[
                    _assignment(span_start_biennium="2019-20", span_end_biennium="2021-22")
                ],
                sponsors=[
                    _sponsor("100", "2017-18"),
                    _sponsor("100", "2019-20"),
                    _sponsor("100", "2021-22"),
                ],
            )
        )
        assert _cited(rows, "assignment", "100:party:D:2019-20") == {
            (WSL, "sponsors:2019-20"),
            (WSL, "sponsors:2021-22"),
        }

    def test_a_committee_span_cites_only_its_own_committee(self) -> None:
        rows, _ = citation_rows(
            _inputs(
                assignments=[
                    _assignment(
                        span_kind="committee",
                        span_discriminator="5",
                        role_key="committee-member-role:5",
                    )
                ],
                committee_members=[
                    _committee_member("100", "5", "2019-20"),
                    _committee_member("100", "9", "2019-20"),
                ],
            )
        )
        assert _cited(rows, "assignment", "100:committee:5:2019-20") == {
            (WSL, "committee-members-hist:2019-20:5:House:Ag")
        }

    def test_an_open_span_runs_to_the_newest_attestation(self) -> None:
        rows, _ = citation_rows(
            _inputs(
                assignments=[_assignment(span_end_biennium=None, valid_to=None, is_active=True)],
                sponsors=[_sponsor("100", "2019-20"), _sponsor("100", "2021-22")],
            )
        )
        assert _cited(rows, "assignment", "100:party:D:2019-20") == {
            (WSL, "sponsors:2019-20"),
            (WSL, "sponsors:2021-22"),
        }

    def test_a_roster_span_is_cited_even_when_its_years_miss_the_listing(self) -> None:
        """The §5 truncation bound derives a term from the NEXT listing on the
        seat, so a span's own bienniums routinely do not contain the listing
        year that attests it — Gary M. Odegaard's 1987-88 Senate span rests on
        a 1985 listing. A year filter here would silently uncite 49 spans."""
        rows, _ = citation_rows(
            _inputs(
                assignments=[
                    _assignment(
                        member_id="gmodegaard:1969",
                        source=ROSTER_SOURCE,
                        span_kind="chamber-senate",
                        span_discriminator="20",
                        span_start_biennium="1987-88",
                        span_end_biennium="1987-88",
                    )
                ],
                roster=[
                    {
                        "name": "G. Modegaard",
                        "year": 1985,
                        "source": ROSTER_SOURCE,
                        "resource_id": "legroster:2025-06-05",
                    }
                ],
            )
        )
        assert _cited(rows, "assignment", "gmodegaard:1969:chamber-senate:20:1987-88") == {
            (ROSTER_SOURCE, "legroster:2025-06-05")
        }

    def test_a_deepened_wsl_span_falls_back_to_the_registered_fold(self) -> None:
        """#228: the WSL archive starts in 1991, so a WSL-keyed span before it
        is roster-derived and the roster is what attests it."""
        rows, _ = citation_rows(
            _inputs(
                person_crosswalk=[
                    _key(f"{WSL}:100", PERSON_A),
                    _key(f"{ROSTER_SOURCE}:jsmith:1937", PERSON_A),
                ],
                assignments=[
                    _assignment(span_start_biennium="1939-40", span_end_biennium="1945-46")
                ],
                sponsors=[_sponsor("100", "1991-92")],
                roster=[
                    {
                        "name": "J. Smith",
                        "year": 1939,
                        "source": ROSTER_SOURCE,
                        "resource_id": "legroster:2025-06-05",
                    }
                ],
            )
        )
        assert _cited(rows, "assignment", "100:party:D:1939-40") == {
            (ROSTER_SOURCE, "legroster:2025-06-05")
        }

    def test_a_deepened_span_with_no_roster_key_still_cites_the_revision(self) -> None:
        """The link rule only proposes folds with a 1991+ listing, so a member
        who left before then carries no roster key at all. The fold that
        deepened the span is the resolver's, not the registry's — but the
        document is the same one either way, and staging keeps one revision."""
        rows, _ = citation_rows(
            _inputs(
                person_crosswalk=[_key(f"{WSL}:100", PERSON_A)],
                assignments=[
                    _assignment(span_start_biennium="1939-40", span_end_biennium="1945-46")
                ],
                sponsors=[_sponsor("100", "1991-92")],
                roster=[
                    {
                        "name": "Someone Else",
                        "year": 1939,
                        "source": ROSTER_SOURCE,
                        "resource_id": "legroster:2025-06-05",
                    }
                ],
            )
        )
        assert _cited(rows, "assignment", "100:party:D:1939-40") == {
            (ROSTER_SOURCE, "legroster:2025-06-05")
        }

    def test_a_span_inside_the_archive_never_falls_back_to_the_roster(self) -> None:
        """The fallback is bounded by the archive's own floor. A post-1991 span
        with no sponsor row is a REAL gap, and papering it with a roster
        citation would hide the one case worth reporting."""
        rows, counters = citation_rows(
            _inputs(
                person_crosswalk=[
                    _key(f"{WSL}:100", PERSON_A),
                    _key(f"{ROSTER_SOURCE}:jsmith:1937", PERSON_A),
                ],
                assignments=[
                    _assignment(span_start_biennium="2019-20", span_end_biennium="2019-20")
                ],
                sponsors=[_sponsor("999", "1991-92")],
                roster=[
                    {
                        "name": "J. Smith",
                        "year": 1939,
                        "source": ROSTER_SOURCE,
                        "resource_id": "legroster:2025-06-05",
                    }
                ],
            )
        )
        assert _cited(rows, "assignment", "100:party:D:2019-20") == set()
        assert counters["uncited_assignments"] == 1

    def test_a_roster_span_cites_the_revision_that_lists_it(self) -> None:
        rows, _ = citation_rows(
            _inputs(
                assignments=[
                    _assignment(
                        entity_id=PERSON_B,
                        member_id="jsmith:1937",
                        source=ROSTER_SOURCE,
                        span_kind="chamber-house",
                        span_discriminator="14",
                        span_start_biennium="1937-38",
                        span_end_biennium="1937-38",
                    )
                ],
                roster=[
                    {
                        "name": "J. Smith",
                        "year": 1937,
                        "source": ROSTER_SOURCE,
                        "resource_id": "legroster:2025-06-05",
                    }
                ],
            )
        )
        assert _cited(rows, "assignment", "jsmith:1937:chamber-house:14:1937-38") == {
            (ROSTER_SOURCE, "legroster:2025-06-05")
        }

    def test_a_span_no_staging_row_covers_is_counted(self) -> None:
        _, counters = citation_rows(_inputs(assignments=[_assignment()], sponsors=[]))
        assert counters["uncited_assignments"] == 1


class TestRoles:
    def test_a_role_inherits_the_citations_of_its_assignments(self) -> None:
        """A seat is attested by the wires that named someone sitting in it."""
        rows, _ = citation_rows(
            _inputs(
                roles=[{"entity_id": ROLE_A, "role_key": "party-role:D"}],
                assignments=[_assignment()],
                sponsors=[_sponsor("100", "2019-20")],
            )
        )
        assert _cited(rows, "role", ROLE_A) == {(WSL, "sponsors:2019-20")}

    def test_a_role_with_no_registry_ulid_is_counted_not_cited(self) -> None:
        rows, counters = citation_rows(
            _inputs(
                roles=[{"entity_id": None, "role_key": "party-role:D"}],
                assignments=[_assignment()],
                sponsors=[_sponsor("100", "2019-20")],
            )
        )
        assert not [r for r in rows if r["entity_type"] == "role"]
        assert counters["unregistered_roles"] == 1


class TestShape:
    def test_rows_are_deduplicated_and_deterministically_ordered(self) -> None:
        inputs = _inputs(
            person_crosswalk=[_key(f"{WSL}:100", PERSON_A)],
            sponsors=[_sponsor("100", "2019-20"), _sponsor("100", "2019-20")],
        )
        rows, counters = citation_rows(inputs)
        assert len(rows) == 1
        assert counters["citations"] == 1
        assert rows == sorted(rows, key=lambda r: tuple(r[c] for c in citations.CITATION_COLUMNS))

    def test_every_row_carries_exactly_the_declared_columns(self) -> None:
        rows, _ = citation_rows(
            _inputs(
                person_crosswalk=[_key(f"{WSL}:100", PERSON_A)],
                sponsors=[_sponsor("100", "2019-20")],
            )
        )
        assert all(list(row) == citations.CITATION_COLUMNS for row in rows)

    def test_a_staging_row_with_no_provenance_is_refused(self) -> None:
        """Provenance is the whole point; a row that lost it must not pass as
        an uncited entity — that reads as a coverage gap rather than a bug."""
        with pytest.raises(ValueError, match="resource_id"):
            citation_rows(
                _inputs(
                    person_crosswalk=[_key(f"{WSL}:100", PERSON_A)],
                    sponsors=[{"member_id": "100", "biennium": "2019-20"}],
                )
            )
