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
from pydantic import ValidationError
from ulid import ULID

from usa_wa_api.api.v1.schemas import (
    ULID_PATTERN,
    AssignmentSummary,
    CoverageSpan,
    JobHealth,
    SourceCoverageOut,
)


def _assignment(source_id: str) -> AssignmentSummary:
    return AssignmentSummary(
        id=ULID(),
        source="usa_wa_legislature",
        source_id=source_id,
        person_id=None,
        holder_name_raw="Doe, Jane",
        role_id=ULID(),
        valid_from=date(2019, 1, 1),
        valid_to=None,
        is_active=True,
        pm_assignment_id=None,
        archived_at=None,
        deleted_at=None,
        created_at=datetime(2026, 1, 1, tzinfo=UTC),
        updated_at=datetime(2026, 1, 1, tzinfo=UTC),
    )


class TestULIDStr:
    def test_accepts_a_ulid_instance_and_renders_26_char_base32(self):
        ulid = ULID()
        model = AssignmentSummary.model_validate({**_assignment("x").model_dump(), "id": ulid})
        rendered = model.model_dump()["id"]
        assert rendered == str(ulid)
        assert len(rendered) == 26
        assert "-" not in rendered

    def test_converts_a_uuid_instance_to_the_base32_form(self):
        """A ``uuid.UUID`` off the driver must never render as UUID hex."""
        ulid = ULID()
        model = AssignmentSummary.model_validate(
            {**_assignment("x").model_dump(), "id": ulid.to_uuid()}
        )
        assert model.id == str(ulid)
        assert model.id != str(ulid.to_uuid())

    def test_rejects_the_uuid_hex_string_form(self):
        """The ``::text``-cast trap: hyphenated hex is not a ULID and must not pass."""
        ulid = ULID()
        with pytest.raises(ValidationError):
            AssignmentSummary.model_validate(
                {**_assignment("x").model_dump(), "id": str(ulid.to_uuid())}
            )

    def test_pattern_is_crockford_base32(self):
        assert ULID_PATTERN == r"^[0-9A-HJKMNP-TV-Z]{26}$"


class TestAssignmentSpanKey:
    """A tenure span *is* an Assignment (``docs/ONTOLOGY.md`` § 2) and its kind lives
    in the 4-part ``source_id``. The API parses it so a consumer does not have to."""

    def test_parses_the_four_part_span_key(self):
        model = _assignment("12345:chamber-house:ld-5-position-1:2019-20")
        assert model.span_kind == "chamber-house"
        assert model.span_discriminator == "ld-5-position-1"
        assert model.span_start_biennium == "2019-20"

    def test_non_span_source_id_yields_nulls_not_garbage(self):
        model = _assignment("legacy-shape")
        assert model.span_kind is None
        assert model.span_discriminator is None
        assert model.span_start_biennium is None

    def test_span_key_fields_are_serialized(self):
        payload = _assignment("12345:party:democrat:2019-20").model_dump()
        assert payload["span_kind"] == "party"
        assert payload["span_discriminator"] == "democrat"
        assert payload["span_start_biennium"] == "2019-20"


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
