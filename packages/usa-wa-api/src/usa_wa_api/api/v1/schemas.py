"""Response models for ``/api/v1`` — the public contract (#184).

These models *are* the API. Every field name, every nullability, every string
form here is published through OpenAPI and cannot be changed afterwards without
breaking a consumer, so they are declared explicitly rather than derived from the
ORM: an ORM row is an internal shape that changes for internal reasons, and a
model per response is what stops a column rename from being a breaking API change.

Two decisions are load-bearing:

**Identifiers are :data:`ULIDStr`.** Primary keys throughout this repo are ULIDs
stored as PostgreSQL ``uuid`` (see ``clearinghouse_core.db.ulid``). The bytes are
a UUID, so *any* path that goes through the UUID representation — a ``::text``
cast in SQL, handing Pydantic the driver's :class:`uuid.UUID` — renders the
36-character hyphenated hex form. That form is not an id any consumer of this
data can use: Power Map's API takes base32 ULIDs and 404s on hex (project memory:
``reference_ulid_pm_encoding``). :data:`ULIDStr` converts a UUID rather than
accepting it, and rejects a hex string outright.

**Coverage keeps its three statuses.** ``verified``/``assumed``/``absent`` are
the whole point of ``source_coverage`` (#180): ``absent`` is a gap the system
*knows about*, and collapsing it into "no rows" is the silence that table exists
to eliminate. :class:`SourceCoverageOut` therefore reports ``coverage_recorded``
separately from ``items``, and surfaces the ``absent`` subset as
:attr:`~SourceCoverageOut.known_gaps` so a consumer cannot flatten it by
accident.
"""

from datetime import date, datetime
from typing import Annotated, Any, Self
from uuid import UUID

from fastapi import HTTPException, Path, status
from pydantic import BaseModel, BeforeValidator, ConfigDict, Field, computed_field
from ulid import ULID as _ULID

ULID_PATTERN = r"^[0-9A-HJKMNP-TV-Z]{26}$"
"""Crockford base32, 26 characters, excluding I/L/O/U. The hyphenated UUID-hex
form fails this — deliberately."""

SPAN_KEY_PARTS = 4
"""``{member_id}:{kind}:{discriminator}:{start_biennium}`` — the tenure-span
``Assignment.source_id`` shape (``docs/ONTOLOGY.md`` § 2). Any other part count is
a legacy or non-span row and parses to nulls rather than to a wrong answer."""


def _as_ulid_str(value: Any) -> Any:
    """Coerce the three shapes a ULID arrives in into its canonical string form.

    A :class:`ulid.ULID` (what the SQLAlchemy type decorator returns) and a
    :class:`uuid.UUID` (what a raw ``text()`` query or a driver default returns)
    both become base32. Anything else — including a UUID-hex *string* — is passed
    through untouched so the field's pattern rejects it with a normal validation
    error instead of being silently repaired into the wrong id.
    """
    if isinstance(value, _ULID):
        return str(value)
    if isinstance(value, UUID):
        return str(_ULID.from_uuid(value))
    return value


ULIDStr = Annotated[
    str,
    BeforeValidator(_as_ulid_str),
    Field(
        pattern=ULID_PATTERN,
        examples=["01J9ZQ7X8K3M4N5P6Q7R8S9T0V"],
        description="26-character Crockford base32 ULID.",
    ),
]


ULIDPath = Annotated[
    str,
    Path(pattern=ULID_PATTERN, description="26-character Crockford base32 ULID."),
]
"""A ULID path parameter. The pattern rejects the UUID-hex form with a 422 before
the value reaches a query, so a consumer that cast an id to text is told rather
than handed a 404 it will read as "row does not exist"."""


def parse_ulid_path(value: str) -> _ULID:
    """Convert a :data:`ULIDPath`-validated string. Cannot fail — the pattern ran."""
    return _ULID.from_str(value)


def parse_ulid_query(value: str | None, *, field: str) -> _ULID | None:
    """Validate a ULID-valued *filter* parameter, naming the field it came from.

    Query filters are declared as plain strings so the 422 can say which parameter
    was wrong; a bare pattern constraint would report the regex instead. Passing a
    malformed id straight to the query would surface as a 500.
    """
    if value is None:
        return None
    try:
        return _ULID.from_str(value)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f"{field} must be a 26-character ULID",
        ) from exc


class ApiModel(BaseModel):
    """Base for every response model: reads ORM rows by attribute."""

    model_config = ConfigDict(from_attributes=True)


# --------------------------------------------------------------------------- #
# Operations slice
# --------------------------------------------------------------------------- #


class JobHealth(ApiModel):
    """The most recent run of one job slug, from the run ledger (#178).

    ``outcome`` is ``None`` while a run is in flight — and a row that *stays* that
    way is a job that never reported back (killed, OOM, hung). That is a fourth
    state, distinct from ``ok``/``degraded``/``failed``, and :attr:`in_flight`
    names it rather than leaving a consumer to infer it from two nulls.
    """

    job_slug: str
    run_id: ULIDStr
    started_at: datetime
    finished_at: datetime | None = Field(
        default=None, description="`null` while the run is in flight or never reported back."
    )
    outcome: str | None = Field(
        default=None, description="`ok` | `degraded` | `failed`; `null` while in flight."
    )
    in_flight: bool = Field(description="True when the run opened and never closed.")
    age_seconds: float = Field(
        description="Seconds since the run finished, or since it started if still in flight."
    )
    duration_seconds: float | None = Field(default=None, description="`null` while in flight.")
    counters: dict[str, Any] | None = Field(
        default=None, description="The job's own summary object, as recorded."
    )
    git_sha: str | None = None
    host: str | None = None

    @classmethod
    def from_row(cls, row: Any, *, now: datetime) -> Self:
        """Project a ``JobRun`` row, deriving the two clock-relative fields.

        ``now`` is injected rather than read here so the route stamps one instant
        across the whole page — otherwise two jobs on the same page would be aged
        against different clocks.
        """
        finished_at = row.finished_at
        return cls(
            job_slug=row.job_slug,
            run_id=row.id,
            started_at=row.started_at,
            finished_at=finished_at,
            outcome=row.outcome,
            in_flight=finished_at is None,
            age_seconds=(now - (finished_at or row.started_at)).total_seconds(),
            duration_seconds=(
                None if finished_at is None else (finished_at - row.started_at).total_seconds()
            ),
            counters=row.counters,
            git_sha=row.git_sha,
            host=row.host,
        )


class SourceOut(ApiModel):
    """A configured data feed (``clearinghouse_core.sources``)."""

    id: ULIDStr
    slug: str
    name: str
    kind: str = Field(description="Transport family: `soap` | `http` | `csv` | `scrape`.")
    base_url: str | None = None
    reliability: float
    cache_ttl_days: int
    retention_policy: str = Field(description="`operational_cache` | `archival` (#54).")
    jurisdiction_id: ULIDStr
    created_at: datetime
    updated_at: datetime


class CoverageSpan(ApiModel):
    """One audited statement about what a source covers on one dimension (#180)."""

    id: ULIDStr
    dimension: str = Field(
        description="The *axis* of the feed (`election_year`, `sponsor_roster`), not the unit."
    )
    range_start: str
    range_end: str | None = Field(
        default=None, description="`null` means open-ended — the feed still serves."
    )
    status: str = Field(
        description=(
            "`verified` (checked against the live feed on `audited_at`), "
            "`assumed` (believed, never checked), or "
            "`absent` (the feed does **not** serve this range, and we know it)."
        )
    )
    audited_at: datetime
    evidence_citation_id: ULIDStr | None = None
    notes: str | None = None


class SourceCoverageOut(ApiModel):
    """What one source covers — and, distinctly, whether anyone has said.

    ``coverage_recorded=false`` with an empty ``items`` means **nobody has audited
    this source**. It does not mean the source covers nothing. Those are different
    answers and the table (#180) exists precisely so they stop being the same
    silence, so the response says which one it is rather than making a consumer
    guess from an empty list.

    :attr:`known_gaps` repeats the ``absent`` entries from :attr:`items`. The
    redundancy is deliberate: a consumer that renders only ``items`` still shows
    the gaps, and one that wants "what is *missing*" gets it without re-encoding
    the status vocabulary on its side.
    """

    source_slug: str
    source_id: ULIDStr
    coverage_recorded: bool = Field(
        description=(
            "False when no coverage has been recorded for this source at all — "
            "**not audited**, which is distinct from covering nothing."
        )
    )
    items: list[CoverageSpan] = Field(default_factory=list)

    @computed_field
    @property
    def dimensions(self) -> list[str]:
        """Every axis with at least one recorded claim, sorted."""
        return sorted({item.dimension for item in self.items})

    @computed_field
    @property
    def known_gaps(self) -> list[CoverageSpan]:
        """The ``absent`` subset — ranges this source is known *not* to serve."""
        return [item for item in self.items if item.status == "absent"]


class CitationOut(ApiModel):
    """One link from a canonical row back to the fetch that asserted it.

    Flattened across ``citations → fetch_events → sources`` because the question
    the route answers ("how do we know this?") is never satisfied by the citation
    row alone — it needs the URL, the instant, and the feed.
    """

    id: ULIDStr
    entity_type: str
    entity_id: ULIDStr
    field_path: str | None = Field(
        default=None, description="Set when the citation attests one field rather than the row."
    )
    confidence: float
    asserted_at: datetime

    fetch_event_id: ULIDStr
    resource_id: str
    url: str
    fetched_at: datetime
    http_status: int | None = None
    fetch_status: str = Field(description="`ok` | `err` | `skipped`.")
    content_hash: str | None = Field(
        default=None,
        description=(
            "Hex sha256 over the archived bytes. `null` means *unbaselined* "
            "(fetched before #54) — never a mismatch."
        ),
    )

    source_id: ULIDStr
    source_slug: str

    @classmethod
    def from_row(cls, citation: Any, fetch_event: Any, source: Any) -> Self:
        """Assemble the chain from the three joined rows.

        ``content_hash`` is ``LargeBinary`` on the way in; hex on the way out,
        because raw bytes have no JSON form and base64 would be a second encoding
        for a value every other tool in this repo prints as hex.
        """
        return cls(
            id=citation.id,
            entity_type=citation.entity_type,
            entity_id=citation.entity_id,
            field_path=citation.field_path,
            confidence=citation.confidence,
            asserted_at=citation.asserted_at,
            fetch_event_id=fetch_event.id,
            resource_id=fetch_event.resource_id,
            url=fetch_event.url,
            fetched_at=fetch_event.fetched_at,
            http_status=fetch_event.http_status,
            fetch_status=str(fetch_event.status),
            content_hash=(
                fetch_event.content_hash.hex() if fetch_event.content_hash is not None else None
            ),
            source_id=source.id,
            source_slug=source.slug,
        )


# --------------------------------------------------------------------------- #
# Canonical slice
# --------------------------------------------------------------------------- #


class LifecycleFields(ApiModel):
    """The two PM-parity tombstones every identity row carries.

    Both are exposed rather than collapsed into an ``is_live`` boolean: they are
    orthogonal axes with opposite re-fetch semantics (``docs/ONTOLOGY.md`` §
    *Lifecycle axes*), and a consumer that needs to tell "PM archived this" from
    "PM deleted this" cannot recover the distinction from one flag.
    """

    archived_at: datetime | None = Field(
        default=None, description="Mirrors PM's reversible *inactive* gate. The PM id is live."
    )
    deleted_at: datetime | None = Field(
        default=None, description="Terminal tombstone. The PM id is gone."
    )


class PersonIdentifierOut(ApiModel):
    """One external id for a Person (bioguide, ``wsl_member_id``, ``pdc_filer_id``…)."""

    id: ULIDStr
    scheme: str
    value: str
    source: str
    source_id: str


class PersonSummary(LifecycleFields):
    """A human."""

    id: ULIDStr
    source: str
    source_id: str
    name_full: str
    name_first: str | None = None
    name_last: str | None = None
    name_middle: str | None = None
    name_suffix: str | None = None
    name_used: str | None = None
    gender: str | None = None
    pm_person_id: ULIDStr | None = Field(
        default=None, description="Power Map anchor; `null` when never synced."
    )
    created_at: datetime
    updated_at: datetime


class PersonDetail(PersonSummary):
    """A Person plus its external-identifier graph."""

    identifiers: list[PersonIdentifierOut] = Field(default_factory=list)


class OrganizationOut(LifecycleFields):
    """Any non-person legal/political entity, discriminated by ``org_type``."""

    id: ULIDStr
    source: str
    source_id: str
    jurisdiction_id: ULIDStr | None = Field(
        default=None, description="Set for public orgs only; private orgs are global."
    )
    name: str
    short_name: str | None = None
    org_type: str
    acronym: str | None = None
    phone: str | None = None
    parent_organization_id: ULIDStr | None = None
    active: bool = Field(
        description=(
            "PM's operational live-vs-dissolved flag. A **third** axis: a dissolved "
            "committee is inactive, not archived, and stays in every read."
        )
    )
    pm_organization_id: ULIDStr | None = None
    created_at: datetime
    updated_at: datetime


class RoleOut(LifecycleFields):
    """A named slot within an Organization — a template, not an occupancy."""

    id: ULIDStr
    source: str
    source_id: str
    organization_id: ULIDStr
    name: str
    role_type: str
    jurisdiction_id: ULIDStr | None = Field(
        default=None,
        description=(
            "The seat's enduring district identity. Non-null marks a *seat* role; "
            "`null` marks a title role (committee/leadership/staff)."
        ),
    )
    qualifier: str | None = Field(
        default=None, description='Seat disambiguator — "Position 1"/"Position 2"; null in Senate.'
    )
    pm_role_id: ULIDStr | None = None
    created_at: datetime
    updated_at: datetime


def _span_parts(source_id: str) -> tuple[str, str, str] | None:
    """The ``(kind, discriminator, start_biennium)`` triple, or ``None``.

    Returns ``None`` for anything that is not the 4-part span key rather than
    guessing — a legacy ``source_id`` with a different part count is not a span,
    and reporting a wrong kind is worse than reporting no kind.
    """
    parts = source_id.split(":")
    if len(parts) != SPAN_KEY_PARTS:
        return None
    return parts[1], parts[2], parts[3]


class AssignmentSummary(LifecycleFields):
    """Person × Role × period — and therefore also a **tenure span**.

    There is no ``spans`` table: a span *is* an Assignment (``docs/ONTOLOGY.md``
    § 2, *Why there is no spans table*). The span's kind is not a column either —
    it lives in the 4-part ``source_id``, so this model parses it into
    :attr:`span_kind` / :attr:`span_discriminator` / :attr:`span_start_biennium`
    rather than making every consumer re-implement the split. All three are
    ``null`` on a row whose ``source_id`` is not that shape.
    """

    id: ULIDStr
    source: str
    source_id: str
    person_id: ULIDStr | None = Field(
        default=None, description="Null when the occupancy is attested by `holder_name_raw` only."
    )
    holder_name_raw: str | None = None
    role_id: ULIDStr
    valid_from: date
    valid_to: date | None = Field(default=None, description="`null` means open — still serving.")
    is_active: bool
    pm_assignment_id: ULIDStr | None = None
    created_at: datetime
    updated_at: datetime

    @computed_field
    @property
    def span_kind(self) -> str | None:
        """`chamber-senate` | `chamber-house` | `committee` | `party`, or null."""
        parts = _span_parts(self.source_id)
        return None if parts is None else parts[0]

    @computed_field
    @property
    def span_discriminator(self) -> str | None:
        """What the span is scoped to — an LD, a `ld-n-position-p`, a committee id."""
        parts = _span_parts(self.source_id)
        return None if parts is None else parts[1]

    @computed_field
    @property
    def span_start_biennium(self) -> str | None:
        """The biennium the tenure opened in. Keying on the start is what makes
        rebuilds idempotent."""
        parts = _span_parts(self.source_id)
        return None if parts is None else parts[2]


class AssignmentDetail(AssignmentSummary):
    """An Assignment with its provenance chain attached.

    "Who held this seat when, and how do we know" in one request — the question
    #184 names, which otherwise takes a join across three schemas by hand.
    """

    citations: list[CitationOut] = Field(default_factory=list)
