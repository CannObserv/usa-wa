"""Source coverage as data (#180) — what a feed actually covers, and how we know.

:class:`~clearinghouse_core.provenance.Source` records how a feed is *configured*
(``reliability``, ``cache_ttl_days``, ``retention_policy``) but nothing about what it
**covers**: no range, no audit date, no known gaps. The facts that answer "which years of
which fact rest on which archive?" lived instead as module constants declared
independently across adapter packages — ``DEFAULT_ELECTION_FLOOR = 2008`` in three files,
``SWEEP_FLOOR_YEAR = 1991`` in two — and the load-bearing fact that the votewa filings
export retired at 2018 existed only as prose in ``docs/ARCHITECTURE.md``.

That is the mechanism behind "we identify a source, build tooling, and only later learn
its limitations": ``docs/ARCHITECTURE.md`` § *Audit before you build* already says to audit
a feed across its full intended range, but the audit's **output had nowhere to land except
a comment**. Nothing forced a coverage claim to be recorded and nothing could be queried.

Two objects, and the seam between them is the design decision:

* :class:`CoverageClaim` — the **declaration**. A frozen dataclass, pure Python, no
  database. Each adapter package declares its sources' claims in its own ``coverage.py``.
* :class:`SourceCoverage` — the **table**. :func:`seed_source_coverage` writes the declared
  claims against a ``Source`` row so the coverage is queryable next to the provenance it
  describes.

**Why the constants derive from the declaration rather than from a query.** Most of the
floors this replaces are ``argparse`` defaults — read at import/parse time, before any
database is opened, and interpolated into ``--help``. Turning them into DB queries would
make ``--help`` require PostgreSQL and give every CLI a new failure mode for a value that
never changes between audits. So the *claim* is the single source of truth, the constants
are derived from it in pure Python (``PDC_ELECTION_YEARS.floor_year``), and the table is
seeded from the same object. Declaration and table cannot drift, because they are not two
declarations — they are one, projected two ways. The queryable form is what the table
exists for; the import-time form is what a CLI default needs. (The alternative the issue
sketches — builders *querying* the table — remains available for any consumer that already
holds a session, e.g. a read surface or a staleness re-audit job.)

``status`` carries the value the whole design turns on:

* ``verified`` — the range was checked against the live feed on ``audited_at``.
* ``assumed`` — believed, not checked. A claim that has never been audited says so.
* ``absent`` — **the feed does not serve this range, and we know it.** This is what lets a
  known gap be a *fact* the system can answer with rather than the silence a missing row is
  indistinguishable from (the votewa 2020+ retirement is the worked example).
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime
from enum import StrEnum

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, String, Text, UniqueConstraint, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column
from ulid import ULID as _ULID

from clearinghouse_core.db.ulid import ULID
from clearinghouse_core.models import Base, TimestampMixin
from clearinghouse_core.provenance import Source

SCHEMA = "clearinghouse_core"


def _new_ulid() -> _ULID:
    """Default factory for ULID PK columns."""
    return _ULID()


class CoverageStatus(StrEnum):
    """How a coverage claim was established. Enforced by a CHECK constraint."""

    verified = "verified"
    """Checked against the live feed on ``audited_at``."""

    assumed = "assumed"
    """Believed but never checked — an unaudited claim that says so out loud."""

    absent = "absent"
    """The feed does **not** serve this range, and that is a recorded fact rather than a
    missing row. The one status a builder must be able to act on: it turns "we have no data
    for 2020" from silence into an answer."""


STATUSES: tuple[str, ...] = tuple(s.value for s in CoverageStatus)
"""The closed vocabulary. A fourth value is a schema change, not an adapter's choice."""

STATUS_CHECK_NAME = "ck_source_coverage_status"
"""Name of the CHECK constraint pinning :data:`STATUSES`. Shared with the migration."""


def status_check_sql() -> str:
    """Render the ``status`` CHECK expression **from** :data:`STATUSES`.

    The vocabulary is declared once. Two independent copies of the same list — the tuple and a
    hand-written constraint — let a fourth status update Python only and surface as an
    ``IntegrityError`` in production; ``job_runs.outcome`` learned this at CR #191 finding 5 and
    this is the same shape one table over (CR #196 finding 25). ``tests/test_source_coverage.py``
    pins this expression against the migration's own copy, which alembic cannot import without
    coupling a historical migration to a live module.
    """
    values = ", ".join(f"'{status}'" for status in STATUSES)
    return f"status IN ({values})"


@dataclass(frozen=True)
class CoverageClaim:
    """One audited statement about what a source covers on one dimension.

    ``dimension`` names the **axis** of the feed, not the unit — a source can publish
    several (WSL serves ``sponsor_roster`` from 1991-92 but ``committee_membership`` only
    from 1999-00, and conflating them is exactly the confusion this replaces).

    ``range_start`` / ``range_end`` are strings because the unit varies with the dimension:
    a bare election year (``"2008"``) or a WA biennium label (``"1991-92"``). ``range_end``
    of ``None`` means open-ended — the feed still serves. Both forms lead with the year, so
    :attr:`floor_year` serves a year-keyed sweep off a biennium-keyed claim.
    """

    source_slug: str
    dimension: str
    range_start: str
    range_end: str | None
    status: CoverageStatus
    audited_at: date
    notes: str

    def __post_init__(self) -> None:
        if not self.notes.strip():
            raise ValueError(
                f"{self.source_slug}/{self.dimension}: notes is required — a coverage claim "
                "with no record of how the bound was established is the prose-in-a-comment "
                "problem again, one table over"
            )
        for label, bound in (("range_start", self.range_start), ("range_end", self.range_end)):
            # :attr:`floor_year` slices the leading four characters, so a malformed bound would
            # not fail here where it is declared — it would raise ``int()`` at whichever CLI
            # imported the derived constant, far from the typo (CR #196 finding 28).
            if bound is not None and not bound[:4].isdigit():
                raise ValueError(
                    f"{self.source_slug}/{self.dimension}: {label} {bound!r} must lead with a "
                    "four-digit year (a bare year '2008' or a biennium label '1991-92')"
                )
        if self.range_end is not None and self.range_end < self.range_start:
            raise ValueError(
                f"{self.source_slug}/{self.dimension}: range_end {self.range_end!r} precedes "
                f"range_start {self.range_start!r}"
            )

    @property
    def floor_year(self) -> int:
        """The calendar year the range opens on — the leading four characters of either
        bound form (``"1991-92"`` → ``1991``)."""
        return int(self.range_start[:4])

    @property
    def ceiling_year(self) -> int | None:
        """The calendar year the range closes on, or ``None`` when open-ended."""
        return None if self.range_end is None else int(self.range_end[:4])


def claim_for(
    claims: Iterable[CoverageClaim],
    dimension: str,
    *,
    status: CoverageStatus | None = None,
) -> CoverageClaim:
    """The single claim for ``dimension`` — by default whichever one the source **serves**.

    ``status=None`` (the default) means "any served status", i.e. anything that is not
    :attr:`CoverageStatus.absent`. That default is load-bearing: the floor derivations are
    module-level, so pinning a status at the call site made re-auditing an ``assumed`` claim
    into a ``verified`` one raise :class:`LookupError` at **import** and kill the CLI deriving
    the floor — and promoting a claim is the workflow the ``assumed`` status exists to invite
    (CR #196 finding 24). Pass an explicit status only to address a specific claim, which is
    what asking for an ``absent`` gap requires.

    Raises :class:`LookupError` on none and on more than one. A builder asking "what is the
    floor" needs one answer; two matching spans means the declaration is ambiguous and the
    caller must say which it wants, rather than receive a silently-picked first.
    """
    if status is None:
        matches = [
            c for c in claims if c.dimension == dimension and c.status != CoverageStatus.absent
        ]
        label = "served"
    else:
        matches = [c for c in claims if c.dimension == dimension and c.status == status]
        label = status.value
    if not matches:
        raise LookupError(f"no {label} coverage claim for dimension {dimension!r}")
    if len(matches) > 1:
        raise LookupError(
            f"ambiguous: {len(matches)} {label} coverage claims for dimension {dimension!r}"
        )
    return matches[0]


def known_gaps(claims: Iterable[CoverageClaim]) -> tuple[CoverageClaim, ...]:
    """Every ``absent`` claim — the ranges this source is known *not* to serve."""
    return tuple(c for c in claims if c.status == CoverageStatus.absent)


class SourceCoverage(Base, TimestampMixin):
    """What one :class:`~clearinghouse_core.provenance.Source` covers on one dimension.

    Seeded from the declared :class:`CoverageClaim` set by :func:`seed_source_coverage`, so
    the audit's output lands next to the provenance it describes and "what do we actually
    cover?" is a query rather than a grep through adapter comments.

    A source holds one row per (dimension, range_start), which is what lets a served span
    and an ``absent`` span coexist on the same dimension — the votewa filings feed is
    ``verified`` 2008–2018 and ``absent`` 2020–onward, and both are facts.
    """

    __tablename__ = "source_coverage"
    __table_args__ = (
        UniqueConstraint("source_id", "dimension", "range_start", name="uq_source_coverage_span"),
        CheckConstraint(status_check_sql(), name=STATUS_CHECK_NAME),
        {"schema": SCHEMA},
    )

    id: Mapped[_ULID] = mapped_column(ULID(), primary_key=True, default=_new_ulid)
    source_id: Mapped[_ULID] = mapped_column(
        ULID(),
        ForeignKey(f"{SCHEMA}.sources.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    dimension: Mapped[str] = mapped_column(String(64), nullable=False)
    """The axis of the feed this row bounds (``election_year``, ``sponsor_roster``,
    ``committee_membership``) — not the unit."""

    range_start: Mapped[str] = mapped_column(String(32), nullable=False)
    range_end: Mapped[str | None] = mapped_column(String(32), nullable=True)
    """``NULL`` = open-ended: the feed still serves this dimension forward."""

    status: Mapped[str] = mapped_column(String(16), nullable=False)
    """One of :data:`STATUSES`. See :class:`CoverageStatus` — ``absent`` is the load-bearing
    one."""

    audited_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    """When the claim was last established against the feed. A staleness query over this
    column is the automated form of the votewa lesson (a feed's range moving without
    anyone noticing)."""

    evidence_citation_id: Mapped[_ULID | None] = mapped_column(
        ULID(),
        ForeignKey(f"{SCHEMA}.citations.id", ondelete="SET NULL"),
        nullable=True,
    )
    """Optional link to the :class:`~clearinghouse_core.provenance.Citation` recording the
    probe that established the claim."""

    notes: Mapped[str | None] = mapped_column(Text, nullable=True)


async def seed_source_coverage(
    session: AsyncSession, source: Source, claims: Sequence[CoverageClaim]
) -> int:
    """Reconcile ``source``'s coverage rows to ``claims``, returning how many rows changed.

    A **full reconcile**, not an upsert: rows the declaration no longer makes are deleted.
    Keyed on ``(dimension, range_start)``, an upsert alone left a stale row behind whenever a
    re-audit *moved a floor* — the commonest re-audit outcome, and one that changes the row's
    own key — so the source claimed both bounds at once (CR #196 finding 27). Retiring the
    undeclared rows is what makes the docstring's promise true rather than aspirational.

    Idempotent, and idempotent *loudly*: a re-run whose claims still match writes nothing and
    returns 0, while a re-audited claim updates its row in place. Operates in the caller's
    transaction.
    """
    existing = {
        (row.dimension, row.range_start): row
        for row in (
            (
                await session.execute(
                    select(SourceCoverage).where(SourceCoverage.source_id == source.id)
                )
            )
            .scalars()
            .all()
        )
    }

    changed = 0
    for claim in claims:
        audited_at = datetime.combine(claim.audited_at, datetime.min.time(), tzinfo=UTC)
        row = existing.get((claim.dimension, claim.range_start))
        if row is None:
            session.add(
                SourceCoverage(
                    source_id=source.id,
                    dimension=claim.dimension,
                    range_start=claim.range_start,
                    range_end=claim.range_end,
                    status=claim.status.value,
                    audited_at=audited_at,
                    notes=claim.notes,
                )
            )
            changed += 1
            continue
        desired = (claim.range_end, claim.status.value, audited_at, claim.notes)
        if (row.range_end, row.status, row.audited_at, row.notes) == desired:
            continue
        row.range_end, row.status, row.audited_at, row.notes = desired
        changed += 1

    declared = {(claim.dimension, claim.range_start) for claim in claims}
    for key, row in existing.items():
        if key not in declared:
            await session.delete(row)
            changed += 1

    if changed:
        await session.flush()
    return changed
