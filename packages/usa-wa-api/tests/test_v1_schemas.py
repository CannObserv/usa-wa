"""Response-model contract for ``/api/v1`` (#184) — the public API surface.

These are the field names, the nullability and the string forms that leak through
OpenAPI to every consumer, so they are pinned here rather than left to whatever a
route happens to return.

The headline case is :data:`~usa_wa_api.api.v1.schemas.ULIDStr`. Primary keys in
this repo are ULIDs stored as PostgreSQL ``uuid``; a ``::text`` cast — or handing
Pydantic the underlying :class:`uuid.UUID` — yields the **UUID-hex** form, which
Power Map's API 404s on (project memory: ``reference_ulid_pm_encoding``). Every
identifier this API emits must be the 26-character Crockford base32 form.
"""

from datetime import UTC, date, datetime

import pytest
from fastapi import HTTPException
from pydantic import ValidationError
from ulid import ULID

from usa_wa_api.api.v1.schemas import (
    ULID_PATTERN,
    AssignmentSummary,
    CoverageSpan,
    JobHealth,
    PersonSummary,
    SourceCoverageOut,
    span_key,
    split_span_key,
)


def _assignment(**over) -> AssignmentSummary:
    fields = {
        "source": "usa_wa_legislature",
        "member_id": "12345",
        "entity_id": None,
        "role_key": "party-role:democratic",
        "span_kind": "party",
        "span_discriminator": "democratic",
        "span_start_biennium": "2019-20",
        "span_end_biennium": None,
        "valid_from": date(2019, 1, 1),
        "valid_to": None,
        "is_active": True,
    }
    return AssignmentSummary(**(fields | over))


class TestULIDStr:
    def test_accepts_a_ulid_instance_and_renders_26_char_base32(self):
        ulid = ULID()
        rendered = PersonSummary.model_validate({"entity_id": ulid}).model_dump()["entity_id"]
        assert rendered == str(ulid)
        assert len(rendered) == 26
        assert "-" not in rendered

    def test_converts_a_uuid_instance_to_the_base32_form(self):
        """A ``uuid.UUID`` off the driver must never render as UUID hex."""
        ulid = ULID()
        model = PersonSummary.model_validate({"entity_id": ulid.to_uuid()})
        assert model.entity_id == str(ulid)
        assert model.entity_id != str(ulid.to_uuid())

    def test_rejects_the_uuid_hex_string_form(self):
        """The ``::text``-cast trap: hyphenated hex is not a ULID and must not pass."""
        ulid = ULID()
        with pytest.raises(ValidationError):
            PersonSummary.model_validate({"entity_id": str(ulid.to_uuid())})

    def test_pattern_is_crockford_base32(self):
        assert ULID_PATTERN == r"^[0-9A-HJKMNP-TV-Z]{26}$"


class TestAssignmentSpanKey:
    """A tenure span *is* an Assignment (``docs/ONTOLOGY.md`` § 2). Since #313 the
    key's parts are real columns and the *id* is what gets assembled from them —
    the inverse of the old model, which carried a string and parsed it."""

    def test_the_id_is_assembled_from_the_columns(self):
        model = _assignment(span_kind="chamber-house", span_discriminator="ld-5-position-1")
        assert model.assignment_id == "12345:chamber-house:ld-5-position-1:2019-20"
        assert model.model_dump()["assignment_id"] == model.assignment_id

    def test_a_roster_member_id_round_trips_through_its_own_colon(self):
        """#259: `<fold>:<year>` makes the key five segments, and a left-anchored
        split would hand back a member id of `jsmith` and a kind of `1937`."""
        model = _assignment(
            member_id="jsmith:1937",
            span_kind="chamber-senate",
            span_discriminator="28",
            span_start_biennium="1937-38",
        )
        assert model.assignment_id == "jsmith:1937:chamber-senate:28:1937-38"
        assert split_span_key(model.assignment_id) == (
            "jsmith:1937",
            "chamber-senate",
            "28",
            "1937-38",
        )

    def test_split_is_the_inverse_of_assembly(self):
        key = span_key("12345", "party", "democratic", "2019-20")
        assert span_key(*split_span_key(key)) == key

    def test_a_key_with_too_few_parts_is_refused(self):
        """A 422, not a guess: reporting a wrong span kind is worse than none."""
        with pytest.raises(HTTPException) as raised:
            split_span_key("legacy-shape")
        assert raised.value.status_code == 422


class TestSourceCoverageOut:
    """``absent`` is the status the whole table exists for (#180): a gap the system
    *knows about*, distinguishable from one nobody audited. Serialization must not
    flatten the three statuses, and an empty table must not read as "covers nothing"."""

    def _span(self, status: str, dimension: str = "election_year") -> CoverageSpan:
        return CoverageSpan(
            id=ULID(),
            dimension=dimension,
            range_start="2008",
            range_end=None if status != "absent" else "2024",
            status=status,
            audited_at=datetime(2026, 8, 1, tzinfo=UTC),
            evidence_citation_id=None,
            notes="audited",
        )

    def test_preserves_all_three_statuses(self):
        out = SourceCoverageOut(
            source_slug="wa_sos_filings",
            source_id=ULID(),
            coverage_recorded=True,
            items=[self._span(s) for s in ("verified", "assumed", "absent")],
        )
        assert [i.status for i in out.items] == ["verified", "assumed", "absent"]

    def test_known_gaps_are_exactly_the_absent_spans(self):
        spans = [self._span("verified"), self._span("absent"), self._span("assumed")]
        out = SourceCoverageOut(
            source_slug="wa_sos_filings", source_id=ULID(), coverage_recorded=True, items=spans
        )
        assert [g.status for g in out.known_gaps] == ["absent"]

    def test_empty_table_is_unrecorded_not_covers_nothing(self):
        out = SourceCoverageOut(
            source_slug="wa_sos_filings", source_id=ULID(), coverage_recorded=False, items=[]
        )
        assert out.coverage_recorded is False
        assert out.items == []
        assert out.known_gaps == []

    def test_dimensions_lists_each_audited_axis_once(self):
        out = SourceCoverageOut(
            source_slug="wsl",
            source_id=ULID(),
            coverage_recorded=True,
            items=[
                self._span("verified", "sponsor_roster"),
                self._span("absent", "sponsor_roster"),
                self._span("assumed", "committee_membership"),
            ],
        )
        assert out.dimensions == ["committee_membership", "sponsor_roster"]


class TestJobHealth:
    """A row with ``finished_at IS NULL`` is a job that never reported back (#178).
    That is a distinct state from ``failed`` and the schema must say so."""

    def test_in_flight_run_has_null_outcome_and_no_duration(self):
        health = JobHealth.from_row(
            _StubRun(
                job_slug="integrity-sweep",
                started_at=datetime(2026, 8, 6, 12, 0, tzinfo=UTC),
                finished_at=None,
                outcome=None,
            ),
            now=datetime(2026, 8, 6, 12, 30, tzinfo=UTC),
        )
        assert health.in_flight is True
        assert health.outcome is None
        assert health.duration_seconds is None
        assert health.age_seconds == 1800.0

    def test_finished_run_ages_from_finished_at(self):
        health = JobHealth.from_row(
            _StubRun(
                job_slug="integrity-sweep",
                started_at=datetime(2026, 8, 6, 12, 0, tzinfo=UTC),
                finished_at=datetime(2026, 8, 6, 12, 10, tzinfo=UTC),
                outcome="degraded",
            ),
            now=datetime(2026, 8, 6, 12, 30, tzinfo=UTC),
        )
        assert health.in_flight is False
        assert health.outcome == "degraded"
        assert health.duration_seconds == 600.0
        assert health.age_seconds == 1200.0


class _StubRun:
    """A ``JobRun``-shaped row without a database."""

    def __init__(self, *, job_slug, started_at, finished_at, outcome):
        self.id = ULID()
        self.job_slug = job_slug
        self.started_at = started_at
        self.finished_at = finished_at
        self.outcome = outcome
        self.counters = {"processed": 3}
        self.git_sha = "abc123"
        self.host = "usa-wa"
