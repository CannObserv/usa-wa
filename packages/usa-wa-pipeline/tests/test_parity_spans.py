"""The span parity probe's comparator and exit gate (#309, CR round 4).

The probe's whole job is to be un-fool-able: it gates *ratchets* against a
snapshot known to be stale, so every way it could report cleanliness without
having compared anything is a bug. These pin the ways it could — no store, no
roster store, neither oracle — plus both baseline gates in both directions, the
span-kind parse that the roster family's five-segment keys break, and (CR 77/78)
the role dimension the dbt test cannot check.
"""

from datetime import UTC, date, datetime

import pytest

from clearinghouse_core.job import OUTCOME_DEGRADED, OUTCOME_FAILED, OUTCOME_OK
from clearinghouse_core.jurisdictions import Jurisdiction, JurisdictionType
from clearinghouse_core.rawstore import RawStore
from clearinghouse_core.registry import (
    KIND_ORG,
    KIND_PERSON,
    KIND_ROLE,
    RegistryEntity,
    RegistryKey,
)
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


#: The role dimension the standard fixture's three spans name — one per
#: (span_kind, span_discriminator) slot, with the ATTRIBUTES `conformed.roles`
#: derives for each. Seeded as the canonical oracle so a clean run is clean on
#: the role parity too, not merely on the span one; carrying the real
#: role_type/name/qualifier is what makes a drift test mean anything (CR 84).
FIXTURE_ROLES = (
    ("party-role:democratic", "party_member", "Member", None),
    ("seat:senate:ld-14", "state_senator", "Washington State Senator, LD-14", None),
    ("party-role:republican", "party_member", "Member", None),
)
FIXTURE_ROLE_KEYS = tuple(role[0] for role in FIXTURE_ROLES)

#: The org natural keys those slots hang from, in the crosswalk's own shape.
FIXTURE_ORG_KEYS = (
    f"{SOURCE}:party-democratic",
    f"{SOURCE}:usa_wa_senate",
    f"{SOURCE}:party-republican",
)


async def _seed_role(
    db_session,
    *,
    roles_spec: tuple[tuple[str, str, str, str | None], ...] = FIXTURE_ROLES,
    bind_orgs: bool = True,
    bind_roles: bool = True,
    role_source: str = SOURCE,
) -> Role:
    """Seed the canonical role dimension and return the Role assignments hang off.

    Every ``(source_id, role_type, name, qualifier)`` in ``roles_spec`` becomes a
    `canonical.roles` row, because the probe diffs our derived roles against that
    table on all four (CR 77, CR 84). Which Organization each hangs from is not
    compared, so one is minted per slot purely to satisfy `uq_roles_org_name`.
    """
    state_type = JurisdictionType(slug="state", display_name="State")
    db_session.add(state_type)
    await db_session.flush()
    jurisdiction = Jurisdiction(
        slug="usa-wa", name="WA", type_id=state_type.id, recorded_at=datetime.now(UTC)
    )
    db_session.add(jurisdiction)
    await db_session.flush()
    # one Organization per slot: `uq_roles_org_name` makes a Role's name unique
    # within its org, which is the real shape anyway (each party and each
    # chamber is its own Org).
    orgs = [
        Organization(source=SOURCE, source_id=f"org-{i}", name=f"Org {i}", org_type="chamber")
        for i, _ in enumerate(roles_spec)
    ]
    db_session.add_all(orgs)
    await db_session.flush()
    roles = [
        Role(
            source=role_source,
            source_id=source_id,
            organization_id=org.id,
            name=name,
            role_type=role_type,
            qualifier=qualifier,
        )
        for (source_id, role_type, name, qualifier), org in zip(roles_spec, orgs, strict=True)
    ]
    db_session.add_all(roles)
    await db_session.flush()
    if bind_orgs:
        for natural_key in FIXTURE_ORG_KEYS:
            await _bind_key(db_session, natural_key, kind=KIND_ORG)
    if bind_roles:
        # What `registry_seed` does in production: the role's registry entity IS
        # the canonical Role's ULID, so `role_entity_mismatches` is 0 and PM's
        # anchors keep naming the same rows (#313).
        for role in roles:
            await _bind_key(
                db_session,
                f"{SOURCE}:{role.source_id}",
                kind=KIND_ROLE,
                entity_id=role.id,
            )
    return roles[0]


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


async def _bind_key(
    db_session, natural_key: str, *, kind: str = KIND_PERSON, entity_id=None
) -> None:
    """Bind one natural key to a registry entity, so the crosswalk join lands
    and `unregistered_spans` / `unregistered_orgs` / `unregistered_roles` are
    genuinely 0.

    ``entity_id`` pins the entity to an existing id — what the seed does for
    roles (#313), carrying the canonical ULID across so PM's anchors stay valid.
    Without it a fresh entity is minted, which is the persons/orgs shape here.
    """
    entity = (
        RegistryEntity(kind=kind) if entity_id is None else RegistryEntity(kind=kind, id=entity_id)
    )
    db_session.add(entity)
    await db_session.flush()
    db_session.add(
        RegistryKey(
            kind=kind,
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
    role_baseline=0,
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
        role_baseline=role_baseline,
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
    assert result.counters["unregistered_orgs"] == 0
    assert result.counters["integrity_failures"] == []


async def test_an_empty_role_oracle_degrades_but_keeps_the_span_diff(db_session, tmp_path) -> None:
    """The same rule the span oracle follows: with nothing to compare against,
    report "no oracle" — never a fork. Reachable because the filter is on the
    role's SOURCE: a canonical dimension holding only another source's roles has
    none of ours, even though assignments necessarily hang off some Role.

    CR 85: the span comparison is finished by then, so it must survive into the
    degraded result. Discarding a completed diff because a *different* oracle is
    missing is the #331 shape — counters lost on an abnormal exit — chosen
    voluntarily, and it blinds the span probe that was working.
    """
    role = await _seed_role(db_session, role_source="usa_wa_pdc")
    await _seed_assignment(db_session, role, f"100:party:democratic:{CURRENT}")
    result = await _run(db_session, tmp_path, sponsors=[_sponsor("100", CURRENT)])
    assert result.outcome == OUTCOME_DEGRADED
    assert result.counters["empty_canonical_roles"] is True
    # the span half was computed and is reported anyway: the one canonical row
    # is matched, so the divergence is our Senate seat and our roster span
    assert result.counters["spans"] == 3
    assert result.counters["canonical"] == 1
    assert result.counters["missing"] == 0
    assert result.counters["extra"] == 2
    assert result.counters["divergence"] == 2


async def test_role_attributes_are_compared_not_just_keys(db_session, tmp_path) -> None:
    """CR 84: the key set matching is not the whole claim. A role carries a
    role_type, a name and a qualifier, and all three are derived here
    independently of the tier that publishes them."""
    role = await _seed_role(db_session)
    await _seed_assignment(db_session, role, f"100:party:democratic:{CURRENT}")
    await _seed_assignment(db_session, role, f"100:chamber-senate:14:{CURRENT}")
    await _bind_key(db_session, f"{SOURCE}:100")
    await _seed_roster_family(db_session, role)
    result = await _run(db_session, tmp_path, sponsors=[_sponsor("100", CURRENT)])
    assert result.outcome == OUTCOME_OK
    assert result.counters["role_divergence"] == 0
    assert result.counters["role_attribute_mismatches"] == 0


async def test_a_role_type_drift_fails_the_probe(db_session, tmp_path) -> None:
    """The #110 shape, which the round-7 gate could not see: 305 party roles
    churned on local `member` vs PM `party_member` — every KEY identical. A
    classifier that drifts from the tier publishes a role whose meaning differs
    from the one Power Map matches on, so it is gated at zero rather than
    ratcheted: it measures 0 on the live corpus across all three attributes.
    """
    drifted = (
        ("party-role:democratic", "member", "Member", None),
        *FIXTURE_ROLES[1:],
    )
    role = await _seed_role(db_session, roles_spec=drifted)
    await _seed_assignment(db_session, role, f"100:party:democratic:{CURRENT}")
    await _seed_assignment(db_session, role, f"100:chamber-senate:14:{CURRENT}")
    await _bind_key(db_session, f"{SOURCE}:100")
    await _seed_roster_family(db_session, role)
    result = await _run(db_session, tmp_path, sponsors=[_sponsor("100", CURRENT)])
    # the key set is identical — only the classification forked
    assert result.counters["role_divergence"] == 0
    assert result.counters["divergence"] == 0
    assert result.counters["role_attribute_mismatches"] == 1
    assert result.outcome == OUTCOME_FAILED
    assert result.resolved_exit_code() == 1
    assert result.counters["integrity_failures"] == ["role_attribute_mismatches"]


async def test_a_role_name_drift_is_caught_too(db_session, tmp_path) -> None:
    """`name` is derived here from a format string and there by the emitter, so
    it can fork the same way. Cheap to compare, and it measures 0 today."""
    drifted = (
        FIXTURE_ROLES[0],
        ("seat:senate:ld-14", "state_senator", "WA State Senator LD 14", None),
        FIXTURE_ROLES[2],
    )
    role = await _seed_role(db_session, roles_spec=drifted)
    await _seed_assignment(db_session, role, f"100:party:democratic:{CURRENT}")
    await _seed_assignment(db_session, role, f"100:chamber-senate:14:{CURRENT}")
    await _bind_key(db_session, f"{SOURCE}:100")
    await _seed_roster_family(db_session, role)
    result = await _run(db_session, tmp_path, sponsors=[_sponsor("100", CURRENT)])
    assert result.counters["role_attribute_mismatches"] == 1
    assert result.outcome == OUTCOME_FAILED


async def test_a_ratchet_failure_names_which_ratchet_tripped(db_session, tmp_path) -> None:
    """CR 89: two ratchets now share one exit code. `integrity_failures` names
    the offender for the gated counters; the ratchets say which one moved."""
    role = await _seed_role(db_session)
    await _seed_assignment(db_session, role, f"100:party:democratic:{CURRENT}")
    await _seed_assignment(db_session, role, f"100:chamber-senate:14:{CURRENT}")
    await _seed_assignment(db_session, role, f"777:party:republican:{CURRENT}")
    await _bind_key(db_session, f"{SOURCE}:100")
    await _seed_roster_family(db_session, role)
    result = await _run(db_session, tmp_path, sponsors=[_sponsor("100", CURRENT)])
    assert result.outcome == OUTCOME_FAILED
    assert result.counters["ratchet_failures"] == ["divergence"]
    assert result.counters["integrity_failures"] == []


async def test_role_keys_are_diffed_against_the_canonical_dimension(db_session, tmp_path) -> None:
    """CR 77: the dbt test that was supposed to guard the role join cannot fail.

    `roles` is generated by iterating `assignments` through the SAME
    `role_for_span`, so `assignments.role_key ⊆ roles.role_key` holds by
    construction and the dangling-key query is unfalsifiable. The fork worth
    detecting is ours drifting from the tier that already publishes these keys
    to Power Map, so the oracle is `canonical.roles` — which the probe can diff
    because it has already rebuilt every span.
    """
    role = await _seed_role(db_session)
    await _seed_assignment(db_session, role, f"100:party:democratic:{CURRENT}")
    await _seed_assignment(db_session, role, f"100:chamber-senate:14:{CURRENT}")
    await _bind_key(db_session, f"{SOURCE}:100")
    await _seed_roster_family(db_session, role)
    result = await _run(db_session, tmp_path, sponsors=[_sponsor("100", CURRENT)])
    assert result.outcome == OUTCOME_OK
    # one role per slot, both families: two parties and one Senate seat
    assert result.counters["roles"] == 3
    assert result.counters["canonical_roles"] == 3
    assert result.counters["role_divergence"] == 0


async def test_a_role_key_the_canonical_tier_does_not_hold_fails(db_session, tmp_path) -> None:
    """A slot only one side names is a forked derivation — the one failure a
    deterministic key cannot tolerate, because nothing mediates the join. The
    diff is symmetric and unfiltered on purpose: the tier gaining a role family
    we do not publish is as much a signal as us minting one it never saw."""
    role = await _seed_role(
        db_session,
        roles_spec=(
            *FIXTURE_ROLES,
            ("seat:senate:ld-99", "state_senator", "Washington State Senator, LD-99", None),
        ),
    )
    await _seed_assignment(db_session, role, f"100:party:democratic:{CURRENT}")
    await _seed_assignment(db_session, role, f"100:chamber-senate:14:{CURRENT}")
    await _bind_key(db_session, f"{SOURCE}:100")
    await _seed_roster_family(db_session, role)
    result = await _run(db_session, tmp_path, sponsors=[_sponsor("100", CURRENT)])
    assert result.counters["role_divergence"] == 1
    # the SPAN diff is clean — only the role dimension forked
    assert result.counters["divergence"] == 0
    assert result.outcome == OUTCOME_FAILED
    assert result.resolved_exit_code() == 1
    assert result.counters["ratchet_failures"] == ["role_divergence"]


async def test_role_divergence_at_its_baseline_is_ok(db_session, tmp_path) -> None:
    """A ratchet like the span one, and for the same reason: the oracle is a
    snapshot. It measures 0 today — 312 = 312, exact — so the default gates at
    zero, but a knowingly stale role stays expressible rather than forcing the
    gate off."""
    role = await _seed_role(
        db_session,
        roles_spec=(
            *FIXTURE_ROLES,
            ("seat:senate:ld-99", "state_senator", "Washington State Senator, LD-99", None),
        ),
    )
    await _seed_assignment(db_session, role, f"100:party:democratic:{CURRENT}")
    await _seed_assignment(db_session, role, f"100:chamber-senate:14:{CURRENT}")
    await _bind_key(db_session, f"{SOURCE}:100")
    await _seed_roster_family(db_session, role)
    result = await _run(db_session, tmp_path, sponsors=[_sponsor("100", CURRENT)], role_baseline=1)
    assert result.outcome == OUTCOME_OK
    assert result.counters["role_divergence"] == 1


async def test_unregistered_orgs_are_counted_and_gated(db_session, tmp_path) -> None:
    """CR 78: increment 4 computed `unregistered_orgs` inside the roles MODEL and
    threw it away — the exact shape CR 60/68/72 closed for `unregistered_spans`.
    A role whose org is unregistered still publishes (a seat exists whether or
    not the registry has minted its chamber), so nothing else would notice the
    dimension going headless.
    """
    role = await _seed_role(db_session, bind_orgs=False)
    await _seed_assignment(db_session, role, f"100:party:democratic:{CURRENT}")
    await _seed_assignment(db_session, role, f"100:chamber-senate:14:{CURRENT}")
    await _bind_key(db_session, f"{SOURCE}:100")
    await _seed_roster_family(db_session, role)
    result = await _run(db_session, tmp_path, sponsors=[_sponsor("100", CURRENT)])
    assert result.counters["unregistered_orgs"] == 3
    assert result.counters["divergence"] == 0
    assert result.counters["role_divergence"] == 0
    assert result.outcome == OUTCOME_FAILED
    assert result.resolved_exit_code() == 1
    assert result.counters["integrity_failures"] == ["unregistered_orgs"]


async def test_roles_are_derived_from_spans_not_from_registered_rows(db_session, tmp_path) -> None:
    """A slot exists whether or not the person filling it is registered, so the
    role derivation reads the SPANS, not the crosswalk-joined rows — which keeps
    the role parity a statement about the DERIVATION rather than the registry
    (CR 86). From the published rows a registrar gap would report as a phantom
    role fork, sending the operator after the wrong defect, and it would hide a
    genuine fork whose only spans happen to be unregistered."""
    role = await _seed_role(db_session)
    await _seed_assignment(db_session, role, f"100:party:democratic:{CURRENT}")
    await _seed_assignment(db_session, role, f"100:chamber-senate:14:{CURRENT}")
    await _seed_roster_family(db_session, role)
    # no person key for member 100: two of the three spans drop on the join
    result = await _run(db_session, tmp_path, sponsors=[_sponsor("100", CURRENT)])
    assert result.counters["unregistered_spans"] == 2
    assert result.counters["roles"] == 3
    assert result.counters["role_divergence"] == 0


async def test_a_role_the_registry_has_not_reached_is_gated(db_session, tmp_path) -> None:
    """#313: a role with no registry ULID publishes (the dimension row a
    published assignment names must not vanish) but cannot be addressed by the
    API, so the gap is gated rather than merely reported. One run of latency is
    normal — `dbt build -> registrar -> publish` — and the next build closes it.
    """
    role = await _seed_role(db_session, bind_roles=False)
    await _seed_assignment(db_session, role, f"100:party:democratic:{CURRENT}")
    await _seed_assignment(db_session, role, f"100:chamber-senate:14:{CURRENT}")
    await _bind_key(db_session, f"{SOURCE}:100")
    await _seed_roster_family(db_session, role)
    result = await _run(db_session, tmp_path, sponsors=[_sponsor("100", CURRENT)])
    assert result.counters["unregistered_roles"] == 3
    assert result.counters["role_divergence"] == 0
    assert result.outcome == OUTCOME_FAILED
    assert result.counters["integrity_failures"] == ["unregistered_roles"]


async def test_a_role_ulid_that_does_not_match_the_canonical_one_is_gated(
    db_session, tmp_path
) -> None:
    """The entire justification for giving roles a registry is that PM's 312
    anchors name the canonical ULIDs, so the seed carries them across rather
    than minting fresh. A registry entity under a DIFFERENT id means those
    anchors point at nothing — a broken cutover, not a latency."""
    role = await _seed_role(db_session, bind_roles=False)
    # bound, but to freshly minted entities rather than the canonical role ids
    for key in FIXTURE_ROLE_KEYS:
        await _bind_key(db_session, f"{SOURCE}:{key}", kind=KIND_ROLE)
    await _seed_assignment(db_session, role, f"100:party:democratic:{CURRENT}")
    await _seed_assignment(db_session, role, f"100:chamber-senate:14:{CURRENT}")
    await _bind_key(db_session, f"{SOURCE}:100")
    await _seed_roster_family(db_session, role)
    result = await _run(db_session, tmp_path, sponsors=[_sponsor("100", CURRENT)])
    assert result.counters["unregistered_roles"] == 0
    assert result.counters["role_entity_mismatches"] == 3
    assert result.counters["role_divergence"] == 0
    assert result.outcome == OUTCOME_FAILED
    assert result.counters["integrity_failures"] == ["role_entity_mismatches"]
