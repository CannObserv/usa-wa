"""The cohort-provider seam, as Protocols (#189, AR-14).

`docs/ARCHITECTURE.md` has always said the application layer "depends on a **cohort
interface** …, not on a concrete source", and that swapping which archive feeds a fact is
"a one-line provider change". It was not expressible: the seven providers in this workspace
implemented no shared `Protocol` or ABC and were purely duck-typed, so the only way for a
composing module to say what it needed was to name a concrete class — which meant naming the
adapter package that class lived in. That is one of the two mechanisms by which cross-adapter
composition had no home.

**What a cohort provider is.** An *archive-first* reader over one source's provenance: it
re-derives rows offline from the `RawPayload`s that source's harvest wrote, keyed by whatever
that source's cohorts are keyed by (a biennium label, an election year, a
`(biennium, committee)` pair), with a live fetch as the fallback for an un-archived key only.

**Why these are small and several rather than one.** The seven providers key on three
different things and return four different row shapes; a single `CohortProvider` covering all
of them would have to erase to `Any`, which is a Protocol that proves nothing. What is
genuinely shared is narrower, and each Protocol below is written to a real substitution a
consumer performs, with at least one existing implementer. Jurisdiction-specific cohort
shapes belong next to their vocabulary (see `usa_wa_common.ballot.HousePositionCohortProvider`),
not here.

The Protocols are `runtime_checkable` so a conformance test can assert a concrete provider
still satisfies them — `isinstance` checks method *presence* only, which is exactly the
regression worth catching: a renamed accessor silently leaving the seam.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import Protocol, TypeVar, runtime_checkable

from ulid import ULID as _ULID

#: ``(fetch_event_id, fetched_at, resource_id)`` — the archived pull attesting one cohort.
#: ``resource_id`` is the append-only citation's idempotency key. Defined here rather than in
#: `span_emit` (which re-exports it) because it is the vocabulary of the *provider* seam: a
#: cohort key maps to one of these, and that mapping is what a span cites.
CitationTarget = tuple[_ULID, datetime, str]

#: The key a source's cohorts are addressed by — a biennium label (`"2025-26"`), an election
#: year (`2024`), or a composite such as `(biennium, committee_id)`.
K = TypeVar("K")


@runtime_checkable
class AttestedCohortProvider(Protocol[K]):
    """A provider that can name the archived FetchEvent attesting each of its cohort keys.

    This is what makes a provider *citable*: an application emitting a span cites the pull
    that attested each period, and it must be able to do so without knowing which feed the
    provider reads. Implemented by `SosResultsCohortProvider`, `SosFilingCohortProvider` and
    `PdcWinnerCohortProvider`.
    """

    async def citation_events(self) -> Mapping[K, CitationTarget]: ...


@runtime_checkable
class BienniumCohortProvider(Protocol):
    """A biennium-keyed ``{source_id: name}`` cohort — the shape the committee rename
    reconcilers diff.

    The substitution this exists for is real and load-bearing: `reconcile_committee_names`
    diffs two `CommitteeRosterCohortProvider` cohorts while `reconcile_committee_meeting_names`
    diffs two `MeetingCohortProvider` ones, and the *diff* code
    (`committee_name_reconcile.reconcile_names_from_maps`) is already shared between them. This
    names the input contract that sharing rests on.
    """

    async def cohort(self, biennium: str) -> Mapping[str, str]: ...


@runtime_checkable
class ArchivedBienniumCohortProvider(BienniumCohortProvider, Protocol):
    """A biennium cohort provider that can also enumerate which biennia it has archived.

    The rename **chain** and the committee lifecycle window builder need the domain of the
    chain — every biennium with an archived roster — not just one cohort. Implemented by
    `CommitteeRosterCohortProvider`. This is the annotation that lets a Layer-4 reconciler
    depend on "a provider of archived committee rosters" rather than constructing one out of
    a SOAP client it had to import from a Layer-3 transport.
    """

    async def archived_bienniums(self) -> Sequence[str]: ...
