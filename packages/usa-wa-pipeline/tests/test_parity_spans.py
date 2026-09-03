"""The span parity probe's comparator and exit gate (#309, CR round 4).

The probe's whole job is to be un-fool-able: it gates a *ratchet* against a
snapshot known to be stale, so every way it could report cleanliness without
having compared anything is a bug. These pin the three of them — no store, no
roster store, no oracle — plus the baseline gate in both directions and the
span-kind parse that the roster family's five-segment keys break.
"""

from datetime import UTC, date, datetime

import pytest

from clearinghouse_core.job import OUTCOME_DEGRADED, OUTCOME_FAILED, OUTCOME_OK
from clearinghouse_core.jurisdictions import Jurisdiction, JurisdictionType
from clearinghouse_core.rawstore import RawStore
from clearinghouse_core.registry import KIND_PERSON, RegistryEntity, RegistryKey
from clearinghouse_domain_legislative.identity import Assignment, Organization, Person, Role
from usa_wa_pipeline.parity_spans import (
    ROSTER_SOURCE,
    SOS_SOURCE,
    SOURCE,
    owned_kind,
    run_parity,
)

pytestmark = pytest.mark.db

CURRENT = "2025-26"

#: What `_roster_row()` mints: one closed pre-1991 party span in the roster
#: source space. Every run below builds it, because the probe now compares BOTH
#: families — so each test states where it lands.
ROSTER_MEMBER = "wilburcranston:1925"
ROSTER_SPAN_ID = f"{ROSTER_MEMBER}:party:republican:1925-26"


#: One ballot row, so the House family's guard is satisfied. It positions
#: nobody in these fixtures (LD 41 is in no sponsor row), which keeps the
#: arithmetic below about the families each test is actually asserting —
#: `test_conformed_house.py` owns the join itself.
def _ballot_row() -> dict:
    return {
        "election_date": "20081104",
        "race": "Legislative District 41 - State Representative Pos. 1",
        "candidate": "Chris Vance",
        "party": "(Prefers Republican Party)",
        "votes": "30000",
        "percentage_of_total_votes": "60.0",
        "jurisdiction_name": "Legislative",
    }


def _sponsor(member_id: str, biennium: str) -> dict:
    return {
        "biennium": biennium,
        "member_id": member_id,
        "agency": "Senate",
        "name": "Dana Whitfield",
        "long_name": "Senator Whitfield",
        "first_name": "Dana",
        "last_name": "Whitfield",
        "party": "D",
        "district": "14",
    }


def _roster_row() -> dict:
    """One pre-1991 roster member-year. Deliberately a stranger to the sponsor
    corpus: the #228 deepening only emits for WSL-JOINED identities, so this
    keeps the roster tier present (the builder refuses an empty one) without
    adding spans the assertions would have to account for."""
    return {
        "district": "30",
        "chamber": "house",
        "year": "1925",
        "order": "1",
        "name": "Wilbur Cranston",
        "party_token": "R",
        "annotation": None,
    }


def _store(tmp_path, source: str, resource_id: str) -> RawStore:
    """A store with one archived wire — enough to be non-empty; the staging
    readers are injected, so the wire's content never matters here."""
    store = RawStore(tmp_path / source, source)
    run = store.open_run()
    run.record(resource_id, b"w", url="u")
    run.close()
    return store


def _empty_store(tmp_path, source: str) -> RawStore:
    return RawStore(tmp_path / f"{source}-empty", source)


async def _seed_role(db_session) -> Role:
    state_type = JurisdictionType(slug="state", display_name="State")
    db_session.add(state_type)
    await db_session.flush()
    jurisdiction = Jurisdiction(
        slug="usa-wa", name="WA", type_id=state_type.id, recorded_at=datetime.now(UTC)
    )
    db_session.add(jurisdiction)
    await db_session.flush()
    org = Organization(source=SOURCE, source_id="1", name="Senate", org_type="chamber")
    db_session.add(org)
    await db_session.flush()
    role = Role(
        source=SOURCE, source_id="r1", organization_id=org.id, name="Member", role_type="other"
    )
    db_session.add(role)
    await db_session.flush()
    return role


async def _seed_assignment(
    db_session,
    role,
    source_id: str,
    *,
    source: str = SOURCE,
    valid_from: date = date(2025, 1, 1),
    valid_to: date | None = None,
    is_active: bool = True,
) -> None:
    person = Person(source=source, source_id=source_id, name_full="Dana Whitfield")
    db_session.add(person)
    await db_session.flush()
    db_session.add(
        Assignment(
            source=source,
            source_id=source_id,
            person_id=person.id,
            role_id=role.id,
            valid_from=valid_from,
            valid_to=valid_to,
            is_active=is_active,
        )
    )
    await db_session.flush()


async def _bind_key(db_session, natural_key: str) -> None:
    """Bind one person natural key to a fresh registry entity, so the span's
    crosswalk join lands and `unregistered_spans` is genuinely 0."""
    entity = RegistryEntity(kind=KIND_PERSON)
    db_session.add(entity)
    await db_session.flush()
    db_session.add(
        RegistryKey(
            kind=KIND_PERSON,
            natural_key=natural_key,
            entity_id=entity.id,
            registered_by="test",
        )
    )
    await db_session.flush()


async def _seed_roster_family(db_session, role) -> None:
    """The fixture roster identity, present on BOTH sides — its canonical span
    and its registry key — so a test asserting a clean run is clean on the
    roster family too, not merely on the WSL one."""
    await _seed_assignment(
        db_session,
        role,
        ROSTER_SPAN_ID,
        source=ROSTER_SOURCE,
        valid_from=date(1925, 1, 1),
        valid_to=date(1926, 12, 31),
        is_active=False,
    )
    await _bind_key(db_session, f"{ROSTER_SOURCE}:{ROSTER_MEMBER}")


async def _run(
    db_session,
    tmp_path,
    *,
    sponsors,
    baseline=0,
    roster_store=None,
    store=None,
    roster=None,
    sos_results=None,
):
    return await run_parity(
        db_session,
        store if store is not None else _store(tmp_path, SOURCE, "sponsors:2025-26"),
        roster_store
        if roster_store is not None
        else _store(tmp_path, ROSTER_SOURCE, "roster-pdf:2025"),
        _store(tmp_path, SOS_SOURCE, "sos-legresults:20081104"),
        baseline=baseline,
        current_biennium=CURRENT,
        sponsor_rows=lambda s: sponsors,
        committee_member_rows=lambda s: [],
        roster_rows=lambda s: [_roster_row()] if roster is None else roster,
        sos_result_rows=lambda s: [_ballot_row()] if sos_results is None else sos_results,
    )


def test_owned_kind_reads_the_kind_of_a_four_part_key() -> None:
    assert owned_kind("27504:committee:28240:2025-26") == "committee"


def test_owned_kind_reads_the_kind_of_a_five_part_roster_key() -> None:
    """The roster family mints `<fold>:<year>` member ids, so its span keys carry
    FIVE segments. Counting from the left lands on the mint year — which is in
    no kind vocabulary, so every roster row would filter itself out and the
    probe would report a vacuous clean."""
    assert owned_kind("frankgmyers:1919:party:republican:1919-20") == "party"


def test_owned_kind_declines_a_malformed_key() -> None:
    """A key that cannot carry a kind returns None instead of raising: a probe
    must degrade on a malformed row, never crash the nightly chain."""
    assert owned_kind("nonsense") is None


async def test_empty_store_degrades(db_session, tmp_path) -> None:
    result = await _run(db_session, tmp_path, sponsors=[], store=_empty_store(tmp_path, SOURCE))
    assert result.outcome == OUTCOME_DEGRADED
    assert result.counters["empty_store"] is True


async def test_empty_roster_store_degrades(db_session, tmp_path) -> None:
    """CR 62: without the roster tier the #228 deepening is lost and divergence
    explodes — reporting that as a port regression sends the operator after the
    wrong thing."""
    result = await _run(
        db_session,
        tmp_path,
        sponsors=[_sponsor("100", CURRENT)],
        roster_store=_empty_store(tmp_path, ROSTER_SOURCE),
    )
    assert result.outcome == OUTCOME_DEGRADED
    assert result.counters["empty_roster_store"] is True


async def test_empty_canonical_degrades(db_session, tmp_path) -> None:
    """An empty oracle makes every comparison vacuously clean."""
    result = await _run(db_session, tmp_path, sponsors=[_sponsor("100", CURRENT)])
    assert result.outcome == OUTCOME_DEGRADED
    assert result.counters["empty_canonical"] is True


async def test_exact_agreement_is_ok(db_session, tmp_path) -> None:
    role = await _seed_role(db_session)
    for source_id in (f"100:party:democratic:{CURRENT}", f"100:chamber-senate:14:{CURRENT}"):
        await _seed_assignment(db_session, role, source_id)
    await _bind_key(db_session, f"{SOURCE}:100")
    await _seed_roster_family(db_session, role)
    result = await _run(db_session, tmp_path, sponsors=[_sponsor("100", CURRENT)])
    assert result.outcome == OUTCOME_OK
    assert result.counters["divergence"] == 0
    # both families compared: 2 WSL spans + 1 roster span
    assert result.counters["spans"] == result.counters["canonical"] == 3
    assert result.counters["wsl_spans"] == 2
    assert result.counters["roster_spans"] == 1


async def test_divergence_beyond_the_baseline_fails(db_session, tmp_path) -> None:
    role = await _seed_role(db_session)
    await _seed_assignment(db_session, role, f"100:party:democratic:{CURRENT}")
    await _seed_assignment(db_session, role, f"777:party:republican:{CURRENT}")
    result = await _run(db_session, tmp_path, sponsors=[_sponsor("100", CURRENT)])
    assert result.outcome == OUTCOME_FAILED
    assert result.resolved_exit_code() == 1
    # 777 is only canonical; the Senate seat span and the roster span are only ours
    assert result.counters["missing"] == 1
    assert result.counters["extra"] == 2
    assert result.counters["divergence"] == 3


async def test_divergence_at_the_baseline_is_ok(db_session, tmp_path) -> None:
    """The gate is a ratchet: the recorded staleness passes, growth does not."""
    role = await _seed_role(db_session)
    await _seed_assignment(db_session, role, f"100:party:democratic:{CURRENT}")
    await _seed_assignment(db_session, role, f"777:party:republican:{CURRENT}")
    await _bind_key(db_session, f"{SOURCE}:100")
    await _bind_key(db_session, f"{ROSTER_SOURCE}:{ROSTER_MEMBER}")
    result = await _run(db_session, tmp_path, sponsors=[_sponsor("100", CURRENT)], baseline=3)
    assert result.outcome == OUTCOME_OK
    assert result.counters["divergence"] == 3


async def test_the_roster_family_is_compared_in_its_own_key_space(db_session, tmp_path) -> None:
    """Increment 2: the roster family is built and diffed alongside the WSL one.
    The two share a table but not an identity space, so the comparison key is
    (source, source_id) — a canonical roster row the build does not assert is
    `missing`, exactly like a WSL one."""
    role = await _seed_role(db_session)
    await _seed_assignment(
        db_session, role, "frankgmyers:1919:party:republican:1919-20", source=ROSTER_SOURCE
    )
    await _seed_assignment(db_session, role, f"100:party:democratic:{CURRENT}")
    result = await _run(db_session, tmp_path, sponsors=[_sponsor("100", CURRENT)], baseline=4)
    assert result.counters["canonical"] == 2
    assert result.counters["roster_spans"] == 1
    # Gmyers is canonical-only; our Senate seat and our own roster span are ours-only
    assert result.counters["missing"] == 1
    assert result.counters["extra"] == 2


async def test_a_roster_store_that_parses_to_nothing_degrades(db_session, tmp_path) -> None:
    """CR 69: the guard belongs on the ROWS, not the store. A store holding
    wires that yield no roster rows must degrade with a named reason, not raise
    out through the harness's exception route (which loses the counters, #331).
    """
    result = await run_parity(
        db_session,
        _store(tmp_path, SOURCE, "sponsors:2025-26"),
        _store(tmp_path, ROSTER_SOURCE, "roster-pdf:2025"),
        _store(tmp_path, SOS_SOURCE, "sos-legresults:20081104"),
        baseline=0,
        current_biennium=CURRENT,
        sponsor_rows=lambda s: [_sponsor("100", CURRENT)],
        committee_member_rows=lambda s: [],
        roster_rows=lambda s: [],
        sos_result_rows=lambda s: [_ballot_row()],
    )
    assert result.outcome == OUTCOME_DEGRADED
    assert result.counters["empty_roster_rows"] is True


async def test_unregistered_spans_are_reported(db_session, tmp_path) -> None:
    """CR 68: the count of spans dropped on the crosswalk join has to reach an
    operator. It cannot come from the dbt model — a `dbt build` never calls
    `configure_logging`, so that logger emits nothing — so the probe, which
    runs under the job harness, carries it.
    """
    role = await _seed_role(db_session)
    await _seed_assignment(db_session, role, f"100:party:democratic:{CURRENT}")
    result = await _run(db_session, tmp_path, sponsors=[_sponsor("100", CURRENT)], baseline=1)
    # no registry keys seeded, so every span is unregistered — both families
    assert result.counters["unregistered_spans"] == 3
    assert result.counters["registered_spans"] == 0


async def test_a_malformed_oracle_key_is_counted(db_session, tmp_path) -> None:
    """CR 70: a canonical key too short to carry a kind shrinks the comparison
    set. Excluding it is right; excluding it silently is the vacuous-parity
    shape earlier rounds fixed elsewhere."""
    role = await _seed_role(db_session)
    await _seed_assignment(db_session, role, f"100:party:democratic:{CURRENT}")
    await _seed_assignment(db_session, role, "nonsense")
    result = await _run(db_session, tmp_path, sponsors=[_sponsor("100", CURRENT)], baseline=1)
    assert result.counters["unparsable_canonical_keys"] == 1
    assert result.counters["canonical"] == 1


async def test_unregistered_spans_fail_the_probe(db_session, tmp_path) -> None:
    """CR 72: the nightly's OnFailure= alerting fires on the EXIT CODE. A
    counter that only ever reaches journald is the same silence findings 60 and
    68 described — a registrar gap shrinks the published dataset and nobody is
    told. The floor is 0: measured 0 on the live corpus, and the nightly runs
    the registrar BEFORE this probe, so a newly harvested member is already
    bound by the time it runs.
    """
    role = await _seed_role(db_session)
    await _seed_assignment(db_session, role, f"100:party:democratic:{CURRENT}")
    await _seed_assignment(db_session, role, f"100:chamber-senate:14:{CURRENT}")
    await _seed_assignment(
        db_session,
        role,
        ROSTER_SPAN_ID,
        source=ROSTER_SOURCE,
        valid_from=date(1925, 1, 1),
        valid_to=date(1926, 12, 31),
        is_active=False,
    )
    result = await _run(db_session, tmp_path, sponsors=[_sponsor("100", CURRENT)])
    # divergence is 0 — only the unregistered spans are wrong
    assert result.counters["divergence"] == 0
    assert result.counters["unregistered_spans"] == 3
    assert result.outcome == OUTCOME_FAILED
    assert result.resolved_exit_code() == 1
    assert result.counters["integrity_failures"] == ["unregistered_spans"]


async def test_malformed_roster_rows_are_counted_and_gated(db_session, tmp_path) -> None:
    """CR 73: PARTIAL roster malformation trips neither the CR-67 raise (records
    survive) nor anything else — it degrades the #228 deepening quietly. Same
    0 floor, measured 0 on the real corpus."""
    role = await _seed_role(db_session)
    await _seed_assignment(db_session, role, f"100:party:democratic:{CURRENT}")
    await _seed_assignment(db_session, role, f"100:chamber-senate:14:{CURRENT}")
    await _bind_key(db_session, f"{SOURCE}:100")
    await _seed_roster_family(db_session, role)
    result = await _run(
        db_session,
        tmp_path,
        sponsors=[_sponsor("100", CURRENT)],
        roster=[_roster_row(), {**_roster_row(), "year": "not-a-year"}],
    )
    assert result.counters["malformed_roster_rows"] == 1
    assert result.outcome == OUTCOME_FAILED
    assert result.counters["integrity_failures"] == ["malformed_roster_rows"]


async def test_a_clean_run_reports_every_integrity_counter_at_zero(db_session, tmp_path) -> None:
    """The complement: registered spans, no malformed rows, no unparsable keys."""
    role = await _seed_role(db_session)
    await _seed_assignment(db_session, role, f"100:party:democratic:{CURRENT}")
    await _seed_assignment(db_session, role, f"100:chamber-senate:14:{CURRENT}")
    await _bind_key(db_session, f"{SOURCE}:100")
    await _seed_roster_family(db_session, role)
    result = await _run(db_session, tmp_path, sponsors=[_sponsor("100", CURRENT)])
    assert result.outcome == OUTCOME_OK
    assert result.counters["registered_spans"] == 3
    assert result.counters["unregistered_spans"] == 0
    assert result.counters["malformed_roster_rows"] == 0
    assert result.counters["unparsable_canonical_keys"] == 0
    assert result.counters["integrity_failures"] == []
