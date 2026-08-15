"""RosterPdfAdapter — the roster-PDF source adapter (Layer 3), archive-only (#225).

Archives the pristine PDF (#54) under ``legroster:<revision-date>``. The revision date is the
document's own (``Revision Date: June 5, 2025``), which makes the archive key mean *this edition*
rather than *this fetch* — the natural version key for a source that changes ~biennially.

One archive key per revision is also the **citation** granularity (settled on the #219 epic): a
1943 row cites ``legroster:2025-06-05``. That is correct rather than merely tolerable — the
citation names the wire that attested the fact, and one revision of one document is that wire. A
synthetic per-(district, year) target would manufacture a provenance granularity the source does
not possess.

Archive-only, symmetric with both SOS sources: :meth:`normalize` raises, and Phase B re-parses
from the archived bytes via :mod:`usa_wa_adapter_legislature.roster_pdf.cohort`. Drive it through
:meth:`~clearinghouse_core.runner.AdapterRunner.archive_only`.
"""

from __future__ import annotations

from collections.abc import AsyncIterable
from datetime import UTC, datetime

from clearinghouse_core.adapter import BaseAdapter, FetchedPayload, NormalizedBatch, ResourceRef
from clearinghouse_core.logging import get_logger
from usa_wa_adapter_legislature.roster_pdf.coverage import ROSTER_SOURCE_SLUG
from usa_wa_adapter_legislature.roster_pdf.extraction import extract_revision_date
from usa_wa_adapter_legislature.roster_pdf.transport import RosterPdfClient

logger = get_logger(__name__)

#: ``fetch_one`` resource-id prefix for a roster edition.
ROSTER_RESOURCE_PREFIX = "legroster:"


class RosterRevisionMismatch(ValueError):
    """The fetched document stamps a different ``Revision Date`` than the key it would archive to.

    Not an outage and not a retry: a **new edition has been published**. Archiving it under the
    requested key would mislabel the bytes, and every citation minted from them would name an
    edition that never attested the fact. Re-run with the new ``--revision``.
    """


def roster_resource_id(revision: str) -> str:
    """The archive resource id for a revision — ``legroster:<YYYY-MM-DD>``."""
    return f"{ROSTER_RESOURCE_PREFIX}{revision}"


def revision_from_resource_id(resource_id: str) -> str:
    """Recover the revision date from a ``legroster:<YYYY-MM-DD>`` resource id."""
    if not resource_id.startswith(ROSTER_RESOURCE_PREFIX):
        raise ValueError(f"unknown resource_id: {resource_id!r}")
    return resource_id[len(ROSTER_RESOURCE_PREFIX) :]


class RosterPdfAdapter(BaseAdapter):
    """WA Legislature *Members of the Legislature* roster adapter, archive-only (#225)."""

    source_slug = ROSTER_SOURCE_SLUG
    schema_name = ROSTER_SOURCE_SLUG
    jurisdiction_slug = "usa-wa"

    def __init__(self, *, revision: str, client: RosterPdfClient | None = None) -> None:
        self.revision = revision
        self._client = client or RosterPdfClient()

    async def discover(self, since: datetime | None) -> AsyncIterable[ResourceRef]:
        """Yield the single cohort for this revision. One document, one edition, one key."""
        yield ResourceRef(resource_id=roster_resource_id(self.revision))

    async def fetch_one(self, resource_id: str) -> FetchedPayload:
        """Fetch the roster PDF, archiving the pristine bytes (#54).

        The revision is taken from ``resource_id`` rather than from instance state, and the
        fetched document's **own** ``Revision Date`` is verified against it: the archive key
        claims to name an edition, so the bytes must actually be that edition (CR findings 1
        and 8). A stamp we cannot read is a warning, not a refusal — only a *disagreement*
        raises :class:`RosterRevisionMismatch`.

        The resolved URL is stamped onto ``FetchEvent.url`` — after any 404 re-discovery, so the
        archive records where the bytes actually came from rather than where we first looked.
        """
        revision = revision_from_resource_id(resource_id)
        fetched = await self._client.fetch_roster()
        stamped = extract_revision_date(fetched.wire)
        if stamped is None:
            logger.warning(
                "roster_revision_unreadable",
                extra={"expected": revision, "url": fetched.url},
            )
        elif stamped != revision:
            raise RosterRevisionMismatch(
                f"document stamps Revision Date {stamped}, not {revision} — a new edition is "
                f"published; re-run with --revision {stamped}"
            )
        return FetchedPayload(
            url=fetched.url,
            fetched_at=datetime.now(UTC),
            content_type=fetched.content_type,
            body=fetched.wire,
            http_status=200,
            parsed=None,
        )

    async def normalize(self, payload: FetchedPayload) -> NormalizedBatch:
        """Unused — archive-only (#225). Phase B parses from the archive, so the parser can be
        revised and re-run without re-fetching a 5.7MB document."""
        raise NotImplementedError(
            "RosterPdfAdapter is archive-only (#225); parse via roster_pdf.cohort, not normalize"
        )
