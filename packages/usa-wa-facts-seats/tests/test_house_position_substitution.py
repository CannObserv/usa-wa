"""The two SOS archives are substitutable *in fact*, not just in name (CR #196 finding 35).

`docs/ARCHITECTURE.md` says which SOS archive supplies the House Position "is the provider's
concern, not the builder's", and that swapping it is "a one-line provider change". #189 found
that claim was false — the results provider exposed ``house_positions`` and the filings
provider exposed ``house_filings``, so under no name were they interchangeable — and fixed it
by giving both the seam's accessor.

`scripts/tests/test_cohort_seam.py` pins that fix, but only with ``isinstance`` against a
``runtime_checkable`` Protocol, which tests **method presence**. It cannot see the two
returning different shapes, and this workspace configures no type checker, so the docstring's
deferral to "the type checker's job" delegates to a job nobody performs. Presence-only
checking is the same gap #189 found, one level up.

So drive the actual consumer. Both archives are seeded with the *same fact* — Ann Rivers, LD5,
Position 1, Republican, in the 2016 general — in each source's own wire format, and
:func:`~usa_wa_common.ballot.position_for` must answer identically off either provider's map.
That is the sentence the architecture doc claims, and the only form of it that regresses
loudly: a provider whose accessor returns a differently-shaped map fails here rather than at
the next attempt to swap sources during an outage.

Lives in the facts package because that is the layer that legally imports both adapters —
`test_cohort_seam.py` is deliberately DB-free and must stay in the unit tier.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from clearinghouse_core.provenance import FetchEvent, FetchStatus, RawPayload, Source
from usa_wa_adapter_sos.filings.cohort import SosFilingCohortProvider
from usa_wa_adapter_sos.results.cohort import SosResultsCohortProvider
from usa_wa_common.ballot import position_for
from usa_wa_common.names import fold_token

ELECTION_YEAR = 2016
LD = 5
BALLOT_NAME = "Ann Rivers"
PARTY_SLUG = "republican"
#: The normalized ballot qualifier both sources' ``normalize`` yields — the full
#: ``"Position 1"``, not the bare digit the wire spells it with.
EXPECTED_POSITION = "Position 1"
#: What the wire says, in both formats: ``State Representative Pos. 1``.
WIRE_POSITION = "1"


async def _source(session, usa_wa, slug: str, name: str) -> Source:
    row = Source(jurisdiction_id=usa_wa.id, name=name, slug=slug, kind="rest")
    session.add(row)
    await session.flush()
    return row


async def _archive(session, source, resource_id: str, body: bytes) -> None:
    event = FetchEvent(
        source_id=source.id,
        resource_id=resource_id,
        url="https://x",
        fetched_at=datetime.now(UTC),
        http_status=200,
        content_hash=bytes(32),
        status=FetchStatus.ok,
    )
    session.add(event)
    await session.flush()
    session.add(
        RawPayload(
            fetch_event_id=event.id, content_type="text/csv", body=body, size_bytes=len(body)
        )
    )
    await session.flush()


def _filings_wire() -> bytes:
    """The votewa ``WhoFiled`` export shape — candidacies, one row per filing."""
    header = "RaceName,RaceJurisdictionName,BallotName,PartyName\r\n"
    row = (
        f"State Representative Pos. {WIRE_POSITION},Legislative District {LD},"
        f"{BALLOT_NAME},(Prefers Republican Party)\r\n"
    )
    return (header + row).encode()


def _results_wire() -> bytes:
    """The results.vote.wa.gov legislative export shape — contests with vote totals."""
    header = '"Race","Candidate","Party","Votes"\r\n'
    row = (
        f'"LEGISLATIVE DISTRICT {LD} - State Representative Pos. {WIRE_POSITION}",'
        f'"{BALLOT_NAME}","(Prefers Republican Party)",100\r\n'
    )
    return (header + row).encode()


@pytest.fixture
async def both_providers(db_session, usa_wa):
    """One fact, archived once per source, in each source's own wire format."""
    filings_source = await _source(db_session, usa_wa, "usa_wa_sos", "SOS")
    results_source = await _source(db_session, usa_wa, "usa_wa_sos_results", "SOS Results")
    await _archive(db_session, filings_source, f"sos-whofiled:{ELECTION_YEAR}11", _filings_wire())
    await _archive(db_session, results_source, f"sos-legresults:{ELECTION_YEAR}11", _results_wire())
    return (
        SosFilingCohortProvider(session=db_session, source_id=filings_source.id),
        SosResultsCohortProvider(session=db_session, source_id=results_source.id),
    )


async def test_position_for_cannot_tell_the_two_archives_apart(both_providers):
    """The consumer's answer is identical off either provider — the doc's claim, executed."""
    answers = []
    for provider in both_providers:
        by_year = await provider.house_positions()
        assert ELECTION_YEAR in by_year, f"{type(provider).__name__} archived no {ELECTION_YEAR}"
        answers.append(position_for(by_year[ELECTION_YEAR], LD, fold_token("Rivers"), PARTY_SLUG))

    assert answers == [EXPECTED_POSITION, EXPECTED_POSITION], (
        "the two SOS archives resolve the same member to different positions; they are not "
        "substitutable, and docs/ARCHITECTURE.md claims they are"
    )


async def test_both_providers_return_the_same_map_shape(both_providers):
    """``isinstance`` against the Protocol proves the accessor exists, not that it returns
    ``{year: {LD: [HousePosition]}}``. With no type checker configured, this is where a
    shape divergence is caught."""
    for provider in both_providers:
        by_year = await provider.house_positions()
        assert isinstance(by_year, dict), type(provider).__name__
        year_key = next(iter(by_year))
        name = type(provider).__name__
        assert isinstance(year_key, int), f"{name} keys years by {type(year_key)}"
        by_ld = by_year[year_key]
        ld_key = next(iter(by_ld))
        assert isinstance(ld_key, int), f"{name} keys LDs by {type(ld_key)}"
        entry = by_ld[ld_key][0]
        assert {"qualifier", "name_keys", "party_slug"} <= set(vars(entry)), (
            f"{type(provider).__name__} yields {type(entry).__name__}, not a HousePosition"
        )


async def test_both_providers_attest_the_same_year(both_providers):
    """Substitution also has to carry provenance: each archive must cite its own pull for the
    same cohort key, or a swapped source emits spans that trace to nothing."""
    for provider in both_providers:
        events = await provider.citation_events()
        assert ELECTION_YEAR in events, f"{type(provider).__name__} attests no {ELECTION_YEAR}"
        fetch_event_id, fetched_at, resource_id = events[ELECTION_YEAR]
        assert fetch_event_id is not None
        assert fetched_at.tzinfo is not None, "citation timestamps are UTC-aware"
        assert resource_id
