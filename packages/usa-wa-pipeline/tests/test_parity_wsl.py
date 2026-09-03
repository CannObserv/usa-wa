"""The WSL parity probe's comparator (#306), against seeded canonical rows."""

from datetime import UTC, datetime

import pytest

from clearinghouse_core.jurisdictions import Jurisdiction, JurisdictionType
from clearinghouse_core.rawstore import RawStore
from clearinghouse_domain_legislative.identity import Organization, Person
from usa_wa_pipeline.parity_wsl import SOURCE, run_parity

pytestmark = pytest.mark.db


def _store(tmp_path) -> RawStore:
    store = RawStore(tmp_path, SOURCE)
    run = store.open_run()
    run.record("committees-roster:2025-26", b"w", url="u")
    run.record("sponsors:2025-26", b"w2", url="u")
    run.close()
    return store


async def _seed_canonical(db_session, *, committee_ids: list[str], member_ids: list[str]) -> None:
    state_type = JurisdictionType(slug="state", display_name="State")
    db_session.add(state_type)
    await db_session.flush()
    jurisdiction = Jurisdiction(
        slug="usa-wa", name="WA", type_id=state_type.id, recorded_at=datetime.now(UTC)
    )
    db_session.add(jurisdiction)
    await db_session.flush()
    for cid in committee_ids:
        db_session.add(
            Organization(source=SOURCE, source_id=cid, name=f"C{cid}", org_type="committee")
        )
    for mid in member_ids:
        db_session.add(Person(source=SOURCE, source_id=mid, name_full=f"M{mid}"))
    await db_session.flush()


async def test_clean_parity(db_session, tmp_path) -> None:
    await _seed_canonical(db_session, committee_ids=["1", "5"], member_ids=["100"])
    reports = await run_parity(
        db_session,
        _store(tmp_path),
        committee_rows=lambda s: [{"committee_id": "1"}],
        meeting_rows=lambda s: [
            {"committee_id": "5", "committee_agency": "Joint"},
            # House meeting refs are CommitteeService's domain — excluded (#39)
            {"committee_id": "999", "committee_agency": "House"},
        ],
        sponsor_rows=lambda s: [{"member_id": "100"}],
        committee_member_rows=lambda s: [{"member_id": "100"}],
    )
    assert all(r.clean for r in reports)


async def test_divergence_surfaces_both_sides(db_session, tmp_path) -> None:
    await _seed_canonical(db_session, committee_ids=["1", "9"], member_ids=[])
    reports = await run_parity(
        db_session,
        _store(tmp_path),
        committee_rows=lambda s: [{"committee_id": "1"}, {"committee_id": "2"}],
        meeting_rows=lambda s: [],
        sponsor_rows=lambda s: [],
        committee_member_rows=lambda s: [{"member_id": "77"}],
    )
    by_name = {r.dataset: r for r in reports}
    assert by_name["committees"].only_staging == {"2"}
    assert by_name["committees"].only_canonical == {"9"}
    assert by_name["sponsors"].only_staging == {"77"}
