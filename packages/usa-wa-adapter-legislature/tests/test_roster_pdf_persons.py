"""Roster Person minting (#228 Phase B) — the write side of identity resolution.

Only **minted** identities create Persons — a WSL-joined identity's Person already exists
in the WSL source space, and creating a roster twin would be the §2 fork. The natural key
is ``(usa_wa_legislature_roster, <identity key>)``, so a re-run upserts idempotently and a
rebuild from the archive plus the adjudication tables reproduces the same rows.
"""

from __future__ import annotations

from sqlalchemy import select
from ulid import ULID as _ULID

from clearinghouse_domain_legislative.identity import Person
from usa_wa_adapter_legislature.roster_pdf.coverage import ROSTER_SOURCE_SLUG
from usa_wa_adapter_legislature.roster_pdf.identity import (
    IDENTITY_MINTED,
    IDENTITY_WSL,
    RosterIdentity,
)
from usa_wa_adapter_legislature.roster_pdf.normalize import RosterRecord
from usa_wa_adapter_legislature.roster_pdf.persons import (
    mint_roster_persons,
    retire_unasserted_roster_persons,
)


def _rec(name: str, year: int, **kw) -> RosterRecord:
    defaults = dict(district=1, chamber="house", order=1, party_token="D", annotation=None)
    defaults.update(kw)
    return RosterRecord(year=year, name=name, page_number=1, **defaults)


def _minted(key: str, *records: RosterRecord) -> RosterIdentity:
    return RosterIdentity(
        disposition=IDENTITY_MINTED,
        fold=key.split(":")[0],
        key=key,
        wsl_member_id=None,
        records=records,
    )


async def test_mints_a_person_per_minted_identity(db_session) -> None:
    identities = [
        _minted("abcarver:1899", _rec("A. B. Carver", 1899), _rec("A. B. Carver", 1901)),
    ]
    result = await mint_roster_persons(db_session, identities)
    assert result == {"created": 1, "existing": 0, "renamed": 0}
    person = (
        await db_session.execute(
            select(Person).where(
                Person.source == ROSTER_SOURCE_SLUG, Person.source_id == "abcarver:1899"
            )
        )
    ).scalar_one()
    assert person.name_full == "A. B. Carver"


async def test_display_name_is_the_most_recent_listing_suffix_stripped(db_session) -> None:
    """Margaret Hurley's shape: the modern form wins over the marital form; Basich's
    shape: the position suffix is seat metadata, never part of a name."""
    identities = [
        _minted(
            "margarethurley:1953",
            _rec("Margaret (Mrs. Joseph E.) Hurley", 1953),
            _rec("Margaret Hurley", 1973),
        ),
        _minted("bobbasich:1985", _rec("Bob Basich – 19B", 1985, district=19)),
    ]
    await mint_roster_persons(db_session, identities)
    rows = (
        (await db_session.execute(select(Person).where(Person.source == ROSTER_SOURCE_SLUG)))
        .scalars()
        .all()
    )
    names = {p.source_id: p.name_full for p in rows}
    assert names["margarethurley:1953"] == "Margaret Hurley"
    assert names["bobbasich:1985"] == "Bob Basich"


async def test_joined_identities_mint_nothing(db_session) -> None:
    joined = RosterIdentity(
        disposition=IDENTITY_WSL,
        fold="x",
        key=None,
        wsl_member_id="42",
        records=(_rec("Jane Doe", 1985),),
    )
    result = await mint_roster_persons(db_session, [joined])
    assert result == {"created": 0, "existing": 0, "renamed": 0}
    rows = (
        (await db_session.execute(select(Person).where(Person.source == ROSTER_SOURCE_SLUG)))
        .scalars()
        .all()
    )
    assert rows == []


async def test_minting_is_idempotent(db_session) -> None:
    identities = [_minted("abcarver:1899", _rec("A. B. Carver", 1899))]
    await mint_roster_persons(db_session, identities)
    result = await mint_roster_persons(db_session, identities)
    assert result == {"created": 0, "existing": 1, "renamed": 0}
    rows = (
        (await db_session.execute(select(Person).where(Person.source == ROSTER_SOURCE_SLUG)))
        .scalars()
        .all()
    )
    assert len(rows) == 1


async def test_rerun_refreshes_a_changed_display_name(db_session) -> None:
    """CR #89: re-derivability is a property of rows, not just keys. A parse fix that
    corrects a printed form must land on the existing Person — otherwise the archive plus
    the adjudication tables no longer reproduce what is in the database."""
    await mint_roster_persons(db_session, [_minted("abcarver:1899", _rec("A. B. Carvcr", 1899))])
    result = await mint_roster_persons(
        db_session, [_minted("abcarver:1899", _rec("A. B. Carver", 1899))]
    )
    assert result == {"created": 0, "existing": 1, "renamed": 1}
    person = (
        await db_session.execute(
            select(Person).where(
                Person.source == ROSTER_SOURCE_SLUG, Person.source_id == "abcarver:1899"
            )
        )
    ).scalar_one()
    assert person.name_full == "A. B. Carver"


async def test_a_person_the_derivation_no_longer_asserts_is_retired(db_session) -> None:
    """CR/#259: a re-derivation that *joins* a fold it previously minted (the boundary
    probe) leaves the old roster Person behind. Nothing else retires it — minting only
    creates and updates — so it would still be produced to PM as the very duplicate the
    join now prevents. The build must retire it."""
    await mint_roster_persons(db_session, [_minted("pattymurray:1989", _rec("Patty Murray", 1989))])

    # the re-derivation joins her to WSL instead: no minted identity carries her key
    result = await retire_unasserted_roster_persons(db_session, asserted_keys={"other:1901"})

    assert result["retired"] == 1
    person = (
        await db_session.execute(
            select(Person).where(
                Person.source == ROSTER_SOURCE_SLUG, Person.source_id == "pattymurray:1989"
            )
        )
    ).scalar_one()
    assert person.deleted_at is not None


async def test_retirement_leaves_asserted_persons_and_is_idempotent(db_session) -> None:
    """A single unasserted row is under the mass-retire floor, so it retires normally."""
    await mint_roster_persons(db_session, [_minted("abcarver:1899", _rec("A. B. Carver", 1899))])

    first = await retire_unasserted_roster_persons(db_session, asserted_keys={"abcarver:1899"})
    assert first["retired"] == 0
    second = await retire_unasserted_roster_persons(db_session, asserted_keys={"someone:1901"})
    assert second["retired"] == 1
    third = await retire_unasserted_roster_persons(db_session, asserted_keys={"someone:1901"})
    assert third["retired"] == 0  # already tombstoned; not re-counted


async def test_an_empty_assertion_skips_the_retirement_sweep(db_session) -> None:
    """CR #106: ``asserted_keys`` is built from the run's identities, so an empty set means
    the derivation produced nothing — a parse regression that drops the pre-1991 rows passes
    the oracle trivially (0 == 0) and would reach here. Retiring on it wipes the whole
    corpus. The span sweep already refuses this; the Person sweep must too."""
    await mint_roster_persons(db_session, [_minted("abcarver:1899", _rec("A. B. Carver", 1899))])

    result = await retire_unasserted_roster_persons(db_session, asserted_keys=set())

    assert result == {"retired": 0, "anchored": 0, "aborted": False}
    person = (
        await db_session.execute(select(Person).where(Person.source_id == "abcarver:1899"))
    ).scalar_one()
    assert person.deleted_at is None


async def test_a_mass_retirement_aborts_and_changes_nothing(db_session) -> None:
    """Past the floor, retiring more than the fraction is a truncated derivation, not a
    cohort that legitimately vanished — abort and leave every row alive."""
    identities = [_minted(f"member{i}:1899", _rec(f"Member {i}", 1899)) for i in range(10)]
    await mint_roster_persons(db_session, identities)

    result = await retire_unasserted_roster_persons(db_session, asserted_keys={"member0:1899"})

    assert result["aborted"] is True
    assert result["retired"] == 0
    rows = (
        (await db_session.execute(select(Person).where(Person.source == ROSTER_SOURCE_SLUG)))
        .scalars()
        .all()
    )
    assert all(r.deleted_at is None for r in rows)


async def test_retirement_refuses_to_tombstone_an_anchored_person(db_session) -> None:
    """The #95 lesson, applied to Persons: a PM-anchored row must not be soft-deleted —
    every recovery path filters ``deleted_at IS NULL``, so the anchor would be orphaned
    with no way back. Count it, leave it, let the caller act."""
    await mint_roster_persons(db_session, [_minted("anchored:1899", _rec("An Chored", 1899))])
    person = (
        await db_session.execute(select(Person).where(Person.source_id == "anchored:1899"))
    ).scalar_one()
    person.pm_person_id = _ULID()
    await db_session.flush()

    result = await retire_unasserted_roster_persons(db_session, asserted_keys=set())

    assert result == {"retired": 0, "anchored": 1, "aborted": False}
    assert person.deleted_at is None
