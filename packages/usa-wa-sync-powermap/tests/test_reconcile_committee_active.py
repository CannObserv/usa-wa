"""Producer-side committee ``active`` reconciliation against the biennium roster (#44).

A committee the WSL ``GetCommittees(biennium)`` roster no longer lists is dissolved
(``active=false``); one that reappears is revived (``active=true``). usa-wa drives both
— but retirement only under the narrowed conditions resolved in
``docs/specs/2026-06-18-transformation-wsl-soap.md`` (Lossy ← item 8): an **explicit**
biennium membership diff (not current-only ``GetActiveCommittees``), guarded by an
empty/short-pull check and a cohort floor, emitted one-shot via the producer ``active``
field — never routine ``to_observation``.

These tests pin the diff, both guardrails, automatic reactivation (which self-heals a
modest-partial-pull false retirement on the next clean run), per-row eligibility (skip
archived / deleted / unanchored / other-source), and per-row failure isolation.
"""

from datetime import UTC, datetime

import pytest
from ulid import ULID

from clearinghouse_domain_legislative.identity import Organization
from clearinghouse_sync_powermap.client import (
    DeliveryBlockedError,
    ObservationResult,
    PayloadRejectedError,
    RetryableClientError,
)
from clearinghouse_sync_powermap.models import DISPOSITION_AUTO_ATTACHED, DISPOSITION_REJECTED
from usa_wa_sync_powermap.descriptors import OrganizationDescriptor
from usa_wa_sync_powermap.reconcile_committee_active import reconcile_committee_active


class _FakeWSL:
    """Stub WSL committee client — returns a fixed ``GetCommittees(biennium)`` roster."""

    def __init__(self, rows):
        self._rows = rows
        self.calls = []

    async def get_committees(self, biennium):
        self.calls.append(biennium)
        return self._rows


class _FakeRosterProvider:
    """Stub prior-roster provider — maps a biennium to its ``{source_id: name}`` cohort."""

    def __init__(self, cohorts):
        self._cohorts = cohorts
        self.calls = []

    async def cohort(self, biennium):
        self.calls.append(biennium)
        return dict(self._cohorts.get(biennium, {}))


class _FakePM:
    """Stub PM client capturing posted observations; result via a factory callable."""

    def __init__(self, result_factory=None):
        self._result_factory = result_factory or (
            lambda _p: ObservationResult(
                disposition=DISPOSITION_AUTO_ATTACHED, pm_id=ULID(), raw={}
            )
        )
        self.posted = []

    async def post_observation(self, observe_path, payload):
        self.posted.append((observe_path, payload))
        result = self._result_factory(payload)
        if isinstance(result, Exception):
            raise result
        return result


async def _add_committee(
    session,
    *,
    source_id,
    name="Committee",
    anchor=None,
    active=True,
    source="usa_wa_legislature",
    org_type="committee",
):
    row = Organization(
        source=source,
        source_id=source_id,
        name=name,
        org_type=org_type,
        active=active,
        pm_organization_id=anchor,
    )
    session.add(row)
    await session.flush()
    return row


def _roster(*ids):
    return [{"Id": i} for i in ids]


# --- retirement ---------------------------------------------------------------


async def test_absent_anchored_committee_is_retired(db_session, usa_wa):
    """A produced committee missing from the biennium roster → one ``active=false``
    observation keyed by its PM anchor."""
    anchor = ULID()
    await _add_committee(db_session, source_id="100", name="Still Here", anchor=ULID())
    await _add_committee(db_session, source_id="200", name="Defunct", anchor=anchor)
    wsl, pm = _FakeWSL(_roster(100)), _FakePM()

    # Tiny fixture (1 absent of 2) would trip the default cohort floor; this test is
    # about the happy-path retirement, not the floor, so permit it.
    summary = await reconcile_committee_active(
        db_session, OrganizationDescriptor(), wsl, pm, biennium="2025-26", max_absent_fraction=1.0
    )

    assert wsl.calls == ["2025-26"]
    assert len(pm.posted) == 1
    path, payload = pm.posted[0]
    assert path == "/api/v1/orgs/observations"
    assert payload["identifier_type"] == "pm_org_id"
    assert payload["identifier_value"] == str(anchor)
    assert payload["active"] is False
    assert summary["retired"] == 1
    assert summary["absent"] == 1
    assert summary["aborted"] is None


async def test_present_committee_is_not_touched(db_session, usa_wa):
    await _add_committee(db_session, source_id="100", anchor=ULID())
    wsl, pm = _FakeWSL(_roster(100)), _FakePM()

    summary = await reconcile_committee_active(
        db_session, OrganizationDescriptor(), wsl, pm, biennium="2025-26"
    )

    assert pm.posted == []
    assert summary["absent"] == 0
    assert summary["retired"] == 0


# --- reactivation -------------------------------------------------------------


async def test_returning_inactive_committee_is_reactivated(db_session, usa_wa):
    """An ``active=false`` committee that reappears in the roster → ``active=true``."""
    anchor = ULID()
    await _add_committee(db_session, source_id="100", anchor=ULID())  # present, active, untouched
    await _add_committee(db_session, source_id="200", anchor=anchor, active=False)
    wsl, pm = _FakeWSL(_roster(100, 200)), _FakePM()

    summary = await reconcile_committee_active(
        db_session, OrganizationDescriptor(), wsl, pm, biennium="2025-26"
    )

    assert len(pm.posted) == 1
    _path, payload = pm.posted[0]
    assert payload["identifier_value"] == str(anchor)
    assert payload["active"] is True
    assert summary["reactivated"] == 1
    assert summary["returning"] == 1
    assert summary["retired"] == 0


async def test_retire_and_reactivate_in_one_run(db_session, usa_wa):
    """A single pass can retire an absent active committee and reactivate a returning
    inactive one — the self-heal path for a prior partial-pull false retirement."""
    await _add_committee(db_session, source_id="100", anchor=ULID())  # present active — untouched
    await _add_committee(db_session, source_id="200", anchor=ULID())  # absent active — retire
    await _add_committee(db_session, source_id="300", anchor=ULID(), active=False)  # returning

    posts = []
    wsl = _FakeWSL(_roster(100, 300))
    pm = _FakePM(
        lambda p: (
            posts.append(p)
            or ObservationResult(disposition=DISPOSITION_AUTO_ATTACHED, pm_id=ULID(), raw={})
        )
    )

    summary = await reconcile_committee_active(
        db_session, OrganizationDescriptor(), wsl, pm, biennium="2025-26", max_absent_fraction=1.0
    )

    assert summary["retired"] == 1
    assert summary["reactivated"] == 1
    actives = {p["identifier_value"]: p["active"] for p in posts}
    assert set(actives.values()) == {True, False}


async def test_absent_inactive_committee_is_left_alone(db_session, usa_wa):
    """A committee already ``active=false`` AND still absent is neither re-retired nor
    reactivated — the run converges."""
    await _add_committee(db_session, source_id="100", anchor=ULID())
    await _add_committee(db_session, source_id="200", anchor=ULID(), active=False)
    wsl, pm = _FakeWSL(_roster(100)), _FakePM()

    summary = await reconcile_committee_active(
        db_session, OrganizationDescriptor(), wsl, pm, biennium="2025-26"
    )

    assert pm.posted == []
    assert summary["absent"] == 0
    assert summary["returning"] == 0


# --- guardrails ---------------------------------------------------------------


async def test_empty_pull_aborts_and_touches_nothing(db_session, usa_wa):
    """An empty roster must read as a failed pull, not a mass dissolution."""
    await _add_committee(db_session, source_id="100", anchor=ULID())
    await _add_committee(db_session, source_id="200", anchor=ULID(), active=False)
    wsl, pm = _FakeWSL([]), _FakePM()

    summary = await reconcile_committee_active(
        db_session, OrganizationDescriptor(), wsl, pm, biennium="2025-26"
    )

    assert pm.posted == []  # neither retire nor reactivate on a suspect pull
    assert summary["aborted"] == "empty_pull"
    assert summary["retired"] == 0
    assert summary["reactivated"] == 0


async def test_cohort_floor_aborts_on_suspiciously_many_absent(db_session, usa_wa):
    """A non-empty-but-partial pull (too many absent) trips the cohort floor → abort."""
    for sid in ("1", "2", "3", "4"):
        await _add_committee(db_session, source_id=sid, anchor=ULID())
    wsl, pm = _FakeWSL(_roster(1)), _FakePM()  # 3 of 4 absent → 0.75 > default 0.34

    summary = await reconcile_committee_active(
        db_session, OrganizationDescriptor(), wsl, pm, biennium="2025-26"
    )

    assert pm.posted == []
    assert summary["aborted"] == "cohort_floor"
    assert summary["absent"] == 3
    assert summary["retired"] == 0


async def test_cohort_floor_override_allows_higher_turnover(db_session, usa_wa):
    """An operator can raise the floor for a genuine high-turnover biennium."""
    await _add_committee(db_session, source_id="1", anchor=ULID())
    await _add_committee(db_session, source_id="2", anchor=ULID())
    wsl, pm = _FakeWSL(_roster(1)), _FakePM()  # 1 of 2 absent → 0.5

    summary = await reconcile_committee_active(
        db_session,
        OrganizationDescriptor(),
        wsl,
        pm,
        biennium="2025-26",
        max_absent_fraction=0.9,
    )

    assert summary["aborted"] is None
    assert summary["retired"] == 1


async def test_inactive_cohort_does_not_dilute_the_floor(db_session, usa_wa):
    """The floor denominator is the **active** cohort: a pile of already-inactive
    committees must not make the absent fraction look small and defeat the guard."""
    await _add_committee(db_session, source_id="1", anchor=ULID())  # active, present
    await _add_committee(db_session, source_id="2", anchor=ULID())  # active, absent → retire
    for sid in ("90", "91", "92", "93", "94", "95"):  # inactive noise, all absent
        await _add_committee(db_session, source_id=sid, anchor=ULID(), active=False)
    wsl, pm = _FakeWSL(_roster(1)), _FakePM()  # 1 of 2 ACTIVE absent → 0.5 > 0.34

    summary = await reconcile_committee_active(
        db_session, OrganizationDescriptor(), wsl, pm, biennium="2025-26"
    )

    assert summary["aborted"] == "cohort_floor"
    assert pm.posted == []


# --- era scoping (#90) --------------------------------------------------------


async def test_historical_cohort_does_not_trip_cohort_floor(db_session, usa_wa):
    """Post-backfill regression (#90): the historical committee harvest (sub-project 3,
    model A) floods the produced cohort with defunct-era Ids, all ``active=true``. Absent
    from the *current* roster, they read as a mass retirement and trip the cohort floor.
    A ``roster_provider`` scopes the diff to the live era (current ∪ prior roster), so the
    defunct Ids never enter the diff — a genuine prior-biennium retirement still fires."""
    for sid in ("A", "B", "C"):  # current-biennium standing committees (present)
        await _add_committee(db_session, source_id=sid, anchor=ULID())
    d_anchor = ULID()
    await _add_committee(db_session, source_id="D", anchor=d_anchor)  # prior biennium, now gone
    for sid in ("H1", "H2", "H3", "H4", "H5"):  # defunct-era harvest Ids — never current
        await _add_committee(db_session, source_id=sid, anchor=ULID())

    wsl = _FakeWSL(_roster("A", "B", "C"))
    provider = _FakeRosterProvider(
        {"2023-24": {"A": "A", "B": "B", "C": "C", "D": "D"}}  # prior roster: A–D
    )
    pm = _FakePM()

    summary = await reconcile_committee_active(
        db_session,
        OrganizationDescriptor(),
        wsl,
        pm,
        biennium="2025-26",
        roster_provider=provider,
    )

    assert provider.calls == ["2023-24"]  # prior biennium
    assert summary["aborted"] is None  # floor no longer trips
    assert summary["scoped_out"] == 5  # the 5 defunct-era Ids excluded from the diff
    assert summary["absent"] == 1  # only D (prior-active, now absent) retires
    assert summary["retired"] == 1
    assert len(pm.posted) == 1
    _path, payload = pm.posted[0]
    assert payload["identifier_value"] == str(d_anchor)
    assert payload["active"] is False


async def test_defunct_era_committee_is_never_retired(db_session, usa_wa):
    """A defunct-era Id absent from both current and prior rosters is scoped out — not a
    retirement candidate, so nothing is posted for it."""
    await _add_committee(db_session, source_id="A", anchor=ULID())  # present
    await _add_committee(db_session, source_id="OLD", anchor=ULID())  # defunct, active
    wsl = _FakeWSL(_roster("A"))
    provider = _FakeRosterProvider({"2023-24": {"A": "A"}})  # OLD in neither roster
    pm = _FakePM()

    summary = await reconcile_committee_active(
        db_session, OrganizationDescriptor(), wsl, pm, biennium="2025-26", roster_provider=provider
    )

    assert pm.posted == []
    assert summary["scoped_out"] == 1
    assert summary["absent"] == 0
    assert summary["retired"] == 0


async def test_no_provider_leaves_cohort_unscoped(db_session, usa_wa):
    """Without a ``roster_provider`` the diff spans the whole produced cohort (legacy
    behavior) — ``scoped_out`` is 0 and no prior roster is pulled."""
    await _add_committee(db_session, source_id="100", anchor=ULID())
    await _add_committee(db_session, source_id="200", anchor=ULID())
    wsl, pm = _FakeWSL(_roster(100)), _FakePM()

    summary = await reconcile_committee_active(
        db_session, OrganizationDescriptor(), wsl, pm, biennium="2025-26", max_absent_fraction=1.0
    )

    assert summary["scoped_out"] == 0
    assert summary["absent"] == 1  # 200 still a candidate — unscoped
    assert summary["retired"] == 1


# --- per-row eligibility ------------------------------------------------------


async def test_archived_absent_committee_is_skipped(db_session, usa_wa):
    """An archived committee is already hidden (PM-curated) and PM 422s ``active`` on
    it — it is not in the live cohort, so it is never retired."""
    await _add_committee(db_session, source_id="100", anchor=ULID())  # present, keeps cohort sane
    row = await _add_committee(db_session, source_id="200", anchor=ULID())
    row.archived_at = datetime.now(UTC)
    await db_session.flush()
    wsl, pm = _FakeWSL(_roster(100)), _FakePM()

    summary = await reconcile_committee_active(
        db_session, OrganizationDescriptor(), wsl, pm, biennium="2025-26"
    )

    assert pm.posted == []
    assert summary["absent"] == 0


async def test_deleted_absent_committee_is_skipped(db_session, usa_wa):
    await _add_committee(db_session, source_id="100", anchor=ULID())
    row = await _add_committee(db_session, source_id="200", anchor=ULID())
    row.deleted_at = datetime.now(UTC)
    await db_session.flush()
    wsl, pm = _FakeWSL(_roster(100)), _FakePM()

    summary = await reconcile_committee_active(
        db_session, OrganizationDescriptor(), wsl, pm, biennium="2025-26"
    )

    assert pm.posted == []
    assert summary["absent"] == 0


async def test_unanchored_absent_committee_is_counted_skipped(db_session, usa_wa):
    """An absent committee PM never anchored can't be retired by id — skipped, counted."""
    await _add_committee(db_session, source_id="100", anchor=ULID())
    await _add_committee(db_session, source_id="200", anchor=None)
    wsl, pm = _FakeWSL(_roster(100)), _FakePM()

    summary = await reconcile_committee_active(
        db_session, OrganizationDescriptor(), wsl, pm, biennium="2025-26", max_absent_fraction=1.0
    )

    assert pm.posted == []
    assert summary["absent"] == 1
    assert summary["skipped_unanchored"] == 1
    assert summary["retired"] == 0


async def test_other_source_committee_is_out_of_scope(db_session, usa_wa):
    await _add_committee(db_session, source_id="100", anchor=ULID())
    await _add_committee(db_session, source_id="999", anchor=ULID(), source="usa_wa_pdc")
    wsl, pm = _FakeWSL(_roster(100)), _FakePM()

    summary = await reconcile_committee_active(
        db_session, OrganizationDescriptor(), wsl, pm, biennium="2025-26"
    )

    assert pm.posted == []
    assert summary["cohort"] == 1
    assert summary["absent"] == 0


async def test_dry_run_posts_nothing(db_session, usa_wa):
    await _add_committee(db_session, source_id="100", anchor=ULID())
    await _add_committee(db_session, source_id="200", anchor=ULID())
    await _add_committee(db_session, source_id="300", anchor=ULID(), active=False)
    wsl, pm = _FakeWSL(_roster(100, 300)), _FakePM()

    summary = await reconcile_committee_active(
        db_session,
        OrganizationDescriptor(),
        wsl,
        pm,
        biennium="2025-26",
        dry_run=True,
        max_absent_fraction=1.0,
    )

    assert pm.posted == []
    assert summary["dry_run"] is True
    assert summary["absent"] == 1
    assert summary["returning"] == 1
    assert summary["retired"] == 0
    assert summary["reactivated"] == 0


# --- per-row failure isolation ------------------------------------------------


async def test_rejected_disposition_is_counted(db_session, usa_wa):
    await _add_committee(db_session, source_id="100", anchor=ULID())
    await _add_committee(db_session, source_id="200", anchor=ULID())
    wsl = _FakeWSL(_roster(100))
    pm = _FakePM(lambda _p: ObservationResult(disposition=DISPOSITION_REJECTED, pm_id=None, raw={}))

    summary = await reconcile_committee_active(
        db_session, OrganizationDescriptor(), wsl, pm, biennium="2025-26", max_absent_fraction=1.0
    )

    assert summary["retired"] == 0
    assert summary["rejected"] == 1


async def test_transient_failure_is_isolated(db_session, usa_wa):
    """A per-row transport blip is counted and skipped — a later healthy row delivers."""
    for sid in ("1", "2", "3"):
        await _add_committee(db_session, source_id=sid, anchor=ULID())
    calls = {"n": 0}

    def _flaky(_payload):
        calls["n"] += 1
        if calls["n"] == 1:
            return RetryableClientError("PM 503")
        return ObservationResult(disposition=DISPOSITION_AUTO_ATTACHED, pm_id=ULID(), raw={})

    wsl, pm = _FakeWSL(_roster(1)), _FakePM(_flaky)  # 2 absent
    summary = await reconcile_committee_active(
        db_session, OrganizationDescriptor(), wsl, pm, biennium="2025-26", max_absent_fraction=0.9
    )

    assert summary["failed"] == 1
    assert summary["retired"] == 1


async def test_payload_rejection_exception_is_isolated(db_session, usa_wa):
    await _add_committee(db_session, source_id="100", anchor=ULID())
    await _add_committee(db_session, source_id="200", anchor=ULID())
    wsl = _FakeWSL(_roster(100))
    pm = _FakePM(lambda _p: PayloadRejectedError("422"))

    summary = await reconcile_committee_active(
        db_session, OrganizationDescriptor(), wsl, pm, biennium="2025-26", max_absent_fraction=1.0
    )

    assert summary["rejected"] == 1
    assert summary["retired"] == 0


async def test_unexpected_disposition_is_counted_failed(db_session, usa_wa):
    """A result that is neither anchoring nor rejected (an id-less non-rejected
    disposition) is counted as failed, never silently dropped."""
    await _add_committee(db_session, source_id="100", anchor=ULID())
    await _add_committee(db_session, source_id="200", anchor=ULID())
    wsl = _FakeWSL(_roster(100))
    pm = _FakePM(lambda _p: ObservationResult(disposition="new", pm_id=None, raw={}))

    summary = await reconcile_committee_active(
        db_session, OrganizationDescriptor(), wsl, pm, biennium="2025-26", max_absent_fraction=1.0
    )

    assert summary["retired"] == 0
    assert summary["rejected"] == 0
    assert summary["failed"] == 1


async def test_auth_block_aborts_run(db_session, usa_wa):
    """A global credential failure propagates — no point posting every row to a dead
    endpoint."""
    await _add_committee(db_session, source_id="100", anchor=ULID())
    await _add_committee(db_session, source_id="200", anchor=ULID())
    wsl = _FakeWSL(_roster(100))
    pm = _FakePM(lambda _p: DeliveryBlockedError("403"))

    with pytest.raises(DeliveryBlockedError):
        await reconcile_committee_active(
            db_session,
            OrganizationDescriptor(),
            wsl,
            pm,
            biennium="2025-26",
            max_absent_fraction=1.0,
        )


async def test_other_class_is_excluded_from_active_reconcile(db_session, usa_wa):
    """org_type='other' (the Joint/Other meeting-derived class, #39) is outside this
    cohort: an anchored 'other' org absent from the roster is never retired."""
    await _add_committee(db_session, source_id="100", anchor=ULID())  # present committee
    other = await _add_committee(
        db_session,
        source_id="-140",
        name="Joint Joint Transportation Committee",
        anchor=ULID(),
        org_type="other",  # absent from the roster, but excluded by org_type
    )
    wsl = _FakeWSL(_roster(100))
    pm = _FakePM()

    await reconcile_committee_active(
        db_session, OrganizationDescriptor(), wsl, pm, biennium="2025-26", max_absent_fraction=1.0
    )

    assert pm.posted == []  # nothing retired — the 'other' row was never in the diff
    assert other.active is True
