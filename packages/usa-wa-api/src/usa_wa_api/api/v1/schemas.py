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

**The canonical slice serves the published datasets** (#313). Persons,
organizations, roles, assignments and citations are projections of
``serving.*``, which is loaded from ``published/`` — so these models describe
the *datapackage* contract, not the retired canonical tables. What that dropped,
and what a consumer migrates to, is the table in ``docs/API.md`` §
*Serving-tier migration*.

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
"""``{member_id}:{kind}:{discriminator}:{start_biennium}`` — a span's addressable
identity (``docs/ONTOLOGY.md`` § 2), assembled from the serving tier's columns by
:func:`span_key` and taken apart by :func:`split_span_key`. Since #313 the parts
ARE columns, so this count describes the id a caller passes rather than a
``source_id`` string the API parses; any other part count is refused with a 422
rather than answered with nulls, because there is no longer a legacy row shape
for it to legitimately be."""


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
    """One link from a published entity back to the raw wire that attests it.

    Flattened across ``citations → raw_fetches`` because the question the route
    answers ("how do we know this?") is never satisfied by the citation row
    alone — it needs the digest, the instant and the URL.

    The #313 successor to the Postgres ``Citation`` chain. Three fields of that
    chain are gone rather than renamed: ``id`` (a citation is now identified by
    what it says, not by a row), ``confidence``/``asserted_at`` (a stateless
    join asserts nothing a re-derivation would not re-assert), and
    ``field_path`` (nothing emits field-level citations in the new tier). What
    replaces them is exact: ``sha256`` names the bytes.
    """

    entity_type: str = Field(description="`person` | `organization` | `role` | `assignment`.")
    entity_id: str = Field(
        description=(
            "Registry ULID for person/organization/role; the 4-part span key for an "
            "assignment, which is that row's published identity."
        )
    )
    source: str = Field(description="Raw-store source slug, e.g. `usa_wa_legislature`.")
    resource_id: str = Field(description="The source's own name for the thing fetched.")
    sha256: str | None = Field(
        default=None, description="Hex digest of the archived bytes — the integrity baseline."
    )
    fetched_at: str | None = Field(default=None, description="When the wire was last pulled.")
    url: str | None = Field(
        default=None, description="`null` when the run manifest that recorded it was pruned."
    )
    bytes: int | None = None
    content_type: str | None = None

    @classmethod
    def from_row(cls, citation: Any, fetch: Any | None) -> Self:
        """Assemble one link from the citation and its attestation.

        ``fetch`` is nullable because the join is a LEFT one: a citation whose
        attestation is missing is an integrity break the nightly probe gates at
        zero (``parity_citations.orphan_citations``), and dropping the row here
        would hide from a reader exactly what that probe exists to shout about.
        """
        return cls(
            entity_type=citation.entity_type,
            entity_id=citation.entity_id,
            source=citation.source,
            resource_id=citation.resource_id,
            sha256=None if fetch is None else fetch.sha256,
            fetched_at=None if fetch is None else fetch.fetched_at,
            url=None if fetch is None else fetch.url,
            bytes=None if fetch is None else fetch.bytes,
            content_type=None if fetch is None else fetch.content_type,
        )


# --------------------------------------------------------------------------- #
# Products slice — the published datasets, projected
# --------------------------------------------------------------------------- #


class PersonCrosswalkOut(ApiModel):
    """One natural key bound to a person entity, with its merge tombstone.

    The un-embedded successor to ``PersonIdentifier``: identity is the registry's
    now, so an external id is a *key* rather than a row hanging off a person.
    ``merged_into`` is the only re-point signal a consumer gets.
    """

    natural_key: str = Field(description="`<namespace>:<value>`, e.g. `wa_pdc:7710`.")
    key_namespace: str | None = None
    key_value: str | None = None
    registered_by: str | None = Field(
        default=None, description="`seed` | `registrar` | an adjudication."
    )
    merged_into: ULIDStr | None = Field(
        default=None, description="Set when this key's entity was merged away."
    )


class PersonSummary(ApiModel):
    """A human, as the published `persons` dataset asserts them.

    One row per LIVE registry entity: a merged entity is absent here and reachable
    only through its crosswalk tombstone, which is retraction-as-absence applied
    to identity.
    """

    entity_id: ULIDStr
    name_full: str | None = Field(
        default=None, description="Survivorship already applied: roster > WSL > PDC."
    )
    name_source: str | None = Field(
        default=None, description="Which source won the name — the audit trail."
    )


class PersonDetail(PersonSummary):
    """A person plus every natural key the registry binds to them."""

    identifiers: list[PersonCrosswalkOut] = Field(default_factory=list)


class OrganizationOut(ApiModel):
    """A committee or body, with the newest biennium's attributes."""

    entity_id: ULIDStr
    name: str | None = None
    long_name: str | None = None
    acronym: str | None = None
    agency: str | None = Field(default=None, description="`House` | `Senate` | `Joint` | `Other`.")
    org_type: str | None = Field(default=None, description="`committee` | `other` | …")
    first_biennium: str | None = None
    last_biennium: str | None = None


class RoleOut(ApiModel):
    """A seat or slot — a template, not an occupancy.

    Two identifiers, both published on purpose: ``role_key`` is the deterministic
    structural name (a pure function of the seat, and Power Map's match key), and
    ``entity_id`` is the registry ULID that stays put when the derived key moves.
    """

    role_key: str
    entity_id: ULIDStr | None = Field(
        default=None,
        description="Null for one build only — the nightly registers after the build.",
    )
    role_type: str | None = None
    name: str | None = None
    span_kind: str | None = None
    span_discriminator: str | None = None
    org_source_id: str | None = None
    org_entity_id: ULIDStr | None = None
    district: int | None = Field(default=None, description="LD number; null off a district seat.")
    qualifier: str | None = Field(
        default=None, description='Seat disambiguator — "Position 1"/"Position 2"; null in Senate.'
    )


class AssignmentSummary(ApiModel):
    """Person × Role × period — and therefore also a **tenure span**.

    There is no ``spans`` table: a span *is* an Assignment (``docs/ONTOLOGY.md``
    § 2). Since #313 the span key's parts are real columns rather than positions
    inside a ``source_id`` string, which is what retires the ``split_part``
    workaround (#335) — and what makes the roster family's colon-bearing member
    ids safe to filter on.
    """

    source: str = Field(
        description=(
            "Which identity space `member_id` belongs to: `usa_wa_legislature` "
            "(numeric ids, 1991-) or `usa_wa_legislature_roster` (`<fold>:<year>`, pre-1991)."
        )
    )
    member_id: str
    entity_id: ULIDStr | None = Field(
        default=None, description="The holder's registry ULID; null while unregistered."
    )
    role_key: str | None = None
    span_kind: str = Field(
        description="`chamber-senate` | `chamber-house` | `committee` | `party`."
    )
    span_discriminator: str = Field(
        description="What the span is scoped to — an LD, a position, a committee id."
    )
    span_start_biennium: str
    span_end_biennium: str | None = None
    valid_from: date | None = None
    valid_to: date | None = Field(default=None, description="`null` means open — still serving.")
    is_active: bool | None = None

    @computed_field
    @property
    def assignment_id(self) -> str:
        """The span's addressable identity: ``{member}:{kind}:{disc}:{start}``.

        The same string the citations artifact cites an assignment by, and what
        ``/assignments/{assignment_id}`` takes. Not a ULID: the serving tier keys
        a span structurally, because a span *is* its key.
        """
        return span_key(
            self.member_id, self.span_kind, self.span_discriminator, self.span_start_biennium
        )


class AssignmentDetail(AssignmentSummary):
    """An assignment with its provenance chain attached.

    "Who held this seat when, and how do we know" in one request — the question
    #184 names, which otherwise takes a join across three datasets by hand.
    """

    citations: list[CitationOut] = Field(default_factory=list)


def span_key(member_id: str, kind: str, discriminator: str, start_biennium: str) -> str:
    """The 4-part span key, assembled. The inverse of :func:`split_span_key`."""
    return f"{member_id}:{kind}:{discriminator}:{start_biennium}"


def split_span_key(value: str) -> tuple[str, str, str, str]:
    """A span key back into its parts, **right-anchored** (#259).

    The trailing three segments are fixed, but the member id is not guaranteed
    colon-free: the roster family's identities are ``<fold>:<year>``, so their
    keys carry five segments and a left-to-right split silently excluded that
    entire source — 3,627 rows reporting "nothing in scope" as though it meant
    "nothing stranded".
    """
    parts = value.rsplit(":", 3)
    if len(parts) != SPAN_KEY_PARTS:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=(
                f"assignment id must be {SPAN_KEY_PARTS} colon-separated parts "
                "(member:kind:discriminator:start_biennium)"
            ),
        )
    member, kind, discriminator, start = parts
    return member, kind, discriminator, start
