"""Producer-side committee **rename** detection across a biennium boundary (#46).

WSL exposes no explicit name-change date, but the committee ``Id`` is stable while
``LongName`` changes (usually at a new biennium). So a rename is a diff of WSL's own
rosters: same ``Id`` in ``GetCommittees(prior)`` and ``GetCommittees(current)``, with a
changed ``LongName``. The biennium boundary supplies the validity window — the new name's
``effective_start`` and the prior name's ``effective_end``.

The diff is on **WSL's own** ``LongName`` (normalized), never the locally-held
``Organization.name`` scalar (which is PM-resolved/curated — diffing against it would fire
on PM's canonicalisation and miss renames that already round-tripped). Emit-to-PM-only
(decision #2 in the #45 plan): PM curates ``is_canonical`` and the #45 read mirror brings
the windowed rows back; no local write.

These tests pin the diff, the window evidence, the normalize-equality precision gate, both
guardrails (empty pull / rename storm), per-row eligibility, and failure isolation —
mirroring the #44 ``reconcile_committee_active`` sibling.
"""

from datetime import UTC, date, datetime

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
from usa_wa_sync_powermap.reconcile_committee_names import reconcile_committee_names

BOUNDARY = date(2025, 1, 1)  # biennium_start_date("2025-26")


class _FakeWSL:
    """Stub WSL committee client — maps each biennium to its fixed roster."""

    def __init__(self, rosters):
        self._rosters = rosters  # {biennium: [committee dict, ...]}
        self.calls = []

    async def get_committees(self, biennium):
        self.calls.append(biennium)
        return self._rosters.get(biennium, [])


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
    source="usa_wa_legislature",
    org_type="committee",
):
    row = Organization(
        source=source,
        source_id=source_id,
        name=name,
        org_type=org_type,
        pm_organization_id=anchor,
    )
    session.add(row)
    await session.flush()
    return row


def _committee(cid, long_name):
    return {"Id": cid, "LongName": long_name}


def _rosters(*, prior, current):
    return {"2023-24": list(prior), "2025-26": list(current)}


# --- rename detection ---------------------------------------------------------


async def test_renamed_committee_emits_windowed_name_pair(db_session, usa_wa):
    """Stable Id + changed LongName across the boundary → one observation keyed by the PM
    anchor carrying both windowed name rows."""
    anchor = ULID()
    await _add_committee(db_session, source_id="200", name="New Name", anchor=anchor)
    wsl = _FakeWSL(
        _rosters(prior=[_committee(200, "Old Name")], current=[_committee(200, "New Name")])
    )
    pm = _FakePM()

    summary = await reconcile_committee_names(
        db_session, OrganizationDescriptor(), wsl, pm, biennium="2025-26", max_rename_fraction=1.0
    )

    assert wsl.calls == ["2025-26", "2023-24"]
    assert len(pm.posted) == 1
    path, payload = pm.posted[0]
    assert path == "/api/v1/orgs/observations"
    assert payload["identifier_type"] == "pm_org_id"
    assert payload["identifier_value"] == str(anchor)
    names = {n["name"]: n for n in payload["names"]}
    assert names["Old Name"]["effective_end"] == BOUNDARY.isoformat()
    assert "effective_start" not in names["Old Name"]  # prior start unknown
    assert names["New Name"]["effective_start"] == BOUNDARY.isoformat()
    assert "effective_end" not in names["New Name"]  # current name is open
    assert summary["renamed"] == 1
    assert summary["emitted"] == 1
    assert summary["aborted"] is None


async def test_same_name_refresh_emits_nothing(db_session, usa_wa):
    """No LongName change → no rename, no dated-name churn."""
    await _add_committee(db_session, source_id="200", name="Steady", anchor=ULID())
    wsl = _FakeWSL(_rosters(prior=[_committee(200, "Steady")], current=[_committee(200, "Steady")]))
    pm = _FakePM()

    summary = await reconcile_committee_names(
        db_session, OrganizationDescriptor(), wsl, pm, biennium="2025-26"
    )

    assert pm.posted == []
    assert summary["renamed"] == 0
    assert summary["emitted"] == 0


async def test_normalized_equal_raw_different_is_not_a_rename(db_session, usa_wa):
    """``Ways & Means`` vs ``Ways and Means`` normalise equal → not a rename (the
    precision gate that stops PM-canonicalisation noise firing as a rename)."""
    await _add_committee(db_session, source_id="200", name="Ways and Means", anchor=ULID())
    wsl = _FakeWSL(
        _rosters(
            prior=[_committee(200, "Ways & Means")],
            current=[_committee(200, "Ways and Means")],
        )
    )
    pm = _FakePM()

    summary = await reconcile_committee_names(
        db_session, OrganizationDescriptor(), wsl, pm, biennium="2025-26"
    )

    assert pm.posted == []
    assert summary["renamed"] == 0


async def test_new_committee_is_not_a_rename(db_session, usa_wa):
    """An Id only in the current roster is a create (routine producer), not a rename."""
    await _add_committee(db_session, source_id="100", name="Present", anchor=ULID())
    await _add_committee(db_session, source_id="300", name="Brand New", anchor=ULID())
    wsl = _FakeWSL(
        _rosters(
            prior=[_committee(100, "Present")],
            current=[_committee(100, "Present"), _committee(300, "Brand New")],
        )
    )
    pm = _FakePM()

    summary = await reconcile_committee_names(
        db_session, OrganizationDescriptor(), wsl, pm, biennium="2025-26"
    )

    assert pm.posted == []
    assert summary["renamed"] == 0


async def test_dropped_committee_is_not_a_rename(db_session, usa_wa):
    """An Id only in the prior roster is a #44 retirement, not a rename."""
    await _add_committee(db_session, source_id="100", name="Present", anchor=ULID())
    wsl = _FakeWSL(
        _rosters(
            prior=[_committee(100, "Present"), _committee(200, "Gone")],
            current=[_committee(100, "Present")],
        )
    )
    pm = _FakePM()

    summary = await reconcile_committee_names(
        db_session, OrganizationDescriptor(), wsl, pm, biennium="2025-26"
    )

    assert pm.posted == []
    assert summary["renamed"] == 0


async def test_roster_row_missing_longname_is_dropped(db_session, usa_wa, caplog):
    """A roster row missing LongName can't seed a diff — it's dropped (and logged), so a
    healthy renamed committee alongside it is still detected."""
    await _add_committee(db_session, source_id="200", name="New Name", anchor=ULID())
    wsl = _FakeWSL(
        _rosters(
            prior=[_committee(200, "Old Name"), {"Id": 201}],  # 201 has no LongName
            current=[_committee(200, "New Name"), {"Id": 201, "LongName": "Has One"}],
        )
    )
    pm = _FakePM()

    with caplog.at_level("WARNING"):
        summary = await reconcile_committee_names(
            db_session,
            OrganizationDescriptor(),
            wsl,
            pm,
            biennium="2025-26",
            max_rename_fraction=1.0,
        )

    # 201 dropped from prior → only 200 overlaps → only 200's rename detected.
    assert summary["renamed"] == 1
    assert summary["overlap"] == 1
    assert any("reconcile_names_roster_row_dropped" in r.message for r in caplog.records)


# --- per-row eligibility ------------------------------------------------------


async def test_unanchored_renamed_is_skipped_counted(db_session, usa_wa):
    """A renamed committee PM never anchored can't be attached by id — skipped, counted."""
    await _add_committee(db_session, source_id="200", name="New Name", anchor=None)
    wsl = _FakeWSL(
        _rosters(prior=[_committee(200, "Old Name")], current=[_committee(200, "New Name")])
    )
    pm = _FakePM()

    summary = await reconcile_committee_names(
        db_session, OrganizationDescriptor(), wsl, pm, biennium="2025-26", max_rename_fraction=1.0
    )

    assert pm.posted == []
    assert summary["renamed"] == 1
    assert summary["skipped_unanchored"] == 1
    assert summary["emitted"] == 0


async def test_unproduced_renamed_is_skipped(db_session, usa_wa):
    """A renamed Id with no local row (never produced) is counted-skipped, never emitted."""
    wsl = _FakeWSL(
        _rosters(prior=[_committee(200, "Old Name")], current=[_committee(200, "New Name")])
    )
    pm = _FakePM()

    summary = await reconcile_committee_names(
        db_session, OrganizationDescriptor(), wsl, pm, biennium="2025-26", max_rename_fraction=1.0
    )

    assert pm.posted == []
    assert summary["renamed"] == 1
    assert summary["skipped_unproduced"] == 1


async def test_archived_renamed_is_counted_hidden(db_session, usa_wa):
    """An archived committee is out of the live cohort and PM 422s evidence on it → not
    emitted, counted as *hidden* (still produced, just archived) not *unproduced*."""
    row = await _add_committee(db_session, source_id="200", name="New Name", anchor=ULID())
    row.archived_at = datetime.now(UTC)
    await db_session.flush()
    wsl = _FakeWSL(
        _rosters(prior=[_committee(200, "Old Name")], current=[_committee(200, "New Name")])
    )
    pm = _FakePM()

    summary = await reconcile_committee_names(
        db_session, OrganizationDescriptor(), wsl, pm, biennium="2025-26", max_rename_fraction=1.0
    )

    assert pm.posted == []
    assert summary["skipped_hidden"] == 1
    assert summary["skipped_unproduced"] == 0


async def test_deleted_renamed_is_counted_hidden(db_session, usa_wa):
    """A deleted committee is likewise out of the live cohort → hidden, not emitted."""
    row = await _add_committee(db_session, source_id="200", name="New Name", anchor=ULID())
    row.deleted_at = datetime.now(UTC)
    await db_session.flush()
    wsl = _FakeWSL(
        _rosters(prior=[_committee(200, "Old Name")], current=[_committee(200, "New Name")])
    )
    pm = _FakePM()

    summary = await reconcile_committee_names(
        db_session, OrganizationDescriptor(), wsl, pm, biennium="2025-26", max_rename_fraction=1.0
    )

    assert pm.posted == []
    assert summary["skipped_hidden"] == 1
    assert summary["skipped_unproduced"] == 0


async def test_other_source_renamed_is_out_of_scope(db_session, usa_wa):
    """Only WSL-produced committees are governed; a same-Id row from another source is
    not adopted as the rename target."""
    await _add_committee(
        db_session, source_id="200", name="New Name", anchor=ULID(), source="usa_wa_pdc"
    )
    wsl = _FakeWSL(
        _rosters(prior=[_committee(200, "Old Name")], current=[_committee(200, "New Name")])
    )
    pm = _FakePM()

    summary = await reconcile_committee_names(
        db_session, OrganizationDescriptor(), wsl, pm, biennium="2025-26", max_rename_fraction=1.0
    )

    assert pm.posted == []
    assert summary["skipped_unproduced"] == 1


# --- guardrails ---------------------------------------------------------------


async def test_empty_current_pull_aborts(db_session, usa_wa):
    await _add_committee(db_session, source_id="200", name="New Name", anchor=ULID())
    wsl = _FakeWSL(_rosters(prior=[_committee(200, "Old Name")], current=[]))
    pm = _FakePM()

    summary = await reconcile_committee_names(
        db_session, OrganizationDescriptor(), wsl, pm, biennium="2025-26"
    )

    assert pm.posted == []
    assert summary["aborted"] == "empty_pull"
    assert summary["emitted"] == 0


async def test_empty_prior_pull_aborts(db_session, usa_wa):
    """An empty prior roster makes every current committee look brand-new — a failed pull,
    not a real history, so abort rather than mis-window."""
    await _add_committee(db_session, source_id="200", name="New Name", anchor=ULID())
    wsl = _FakeWSL(_rosters(prior=[], current=[_committee(200, "New Name")]))
    pm = _FakePM()

    summary = await reconcile_committee_names(
        db_session, OrganizationDescriptor(), wsl, pm, biennium="2025-26"
    )

    assert pm.posted == []
    assert summary["aborted"] == "empty_pull"


async def test_low_overlap_aborts(db_session, usa_wa):
    """A thin shared-Id overlap (wrong-biennium pull / Id-scheme change) aborts rather than
    reporting a hollow "renamed: 0" — WSL Ids are stable, so a real diff overlaps heavily."""
    for sid in ("1", "2", "3", "4"):
        await _add_committee(db_session, source_id=sid, name=f"C{sid}", anchor=ULID())
    wsl = _FakeWSL(
        _rosters(
            prior=[_committee(1, "Renamed One")] + [_committee(c, f"X{c}") for c in (91, 92, 93)],
            current=[_committee(int(s), f"C{s}") for s in ("1", "2", "3", "4")],
        )
    )  # overlap = {1}; 1/4 = 0.25 < default 0.5
    pm = _FakePM()

    summary = await reconcile_committee_names(
        db_session, OrganizationDescriptor(), wsl, pm, biennium="2025-26"
    )

    assert pm.posted == []
    assert summary["aborted"] == "low_overlap"
    assert summary["overlap"] == 1
    assert summary["emitted"] == 0


async def test_low_overlap_override_allows_high_growth(db_session, usa_wa):
    """An operator can lower the floor for a biennium that genuinely added many committees."""
    await _add_committee(db_session, source_id="1", name="C1", anchor=ULID())
    wsl = _FakeWSL(
        _rosters(
            prior=[_committee(1, "Old One"), _committee(91, "X91"), _committee(92, "X92")],
            current=[_committee(1, "C1"), _committee(2, "New Two"), _committee(3, "New Three")],
        )
    )  # overlap = {1}; 1/3 = 0.33 < default but allowed at 0.1
    pm = _FakePM()

    summary = await reconcile_committee_names(
        db_session,
        OrganizationDescriptor(),
        wsl,
        pm,
        biennium="2025-26",
        min_overlap_fraction=0.1,
        max_rename_fraction=1.0,
    )

    assert summary["aborted"] is None
    assert summary["renamed"] == 1
    assert summary["emitted"] == 1


async def test_rename_storm_aborts(db_session, usa_wa):
    """A suspiciously-large renamed fraction reads as a normalisation/encoding artifact or
    wrong-biennium pull, not a real mass rename → abort."""
    for sid in ("1", "2", "3", "4"):
        await _add_committee(db_session, source_id=sid, name=f"New{sid}", anchor=ULID())
    wsl = _FakeWSL(
        _rosters(
            prior=[_committee(int(s), f"Old{s}") for s in ("1", "2", "3", "4")],
            current=[_committee(int(s), f"New{s}") for s in ("1", "2", "3", "4")],
        )
    )  # 4 of 4 renamed → 1.0 > default
    pm = _FakePM()

    summary = await reconcile_committee_names(
        db_session, OrganizationDescriptor(), wsl, pm, biennium="2025-26"
    )

    assert pm.posted == []
    assert summary["aborted"] == "rename_storm"
    assert summary["renamed"] == 4
    assert summary["emitted"] == 0


async def test_rename_storm_override_allows_higher_churn(db_session, usa_wa):
    await _add_committee(db_session, source_id="1", name="New1", anchor=ULID())
    await _add_committee(db_session, source_id="2", name="Same", anchor=ULID())
    wsl = _FakeWSL(
        _rosters(
            prior=[_committee(1, "Old1"), _committee(2, "Same")],
            current=[_committee(1, "New1"), _committee(2, "Same")],
        )
    )  # 1 of 2 renamed → 0.5
    pm = _FakePM()

    summary = await reconcile_committee_names(
        db_session,
        OrganizationDescriptor(),
        wsl,
        pm,
        biennium="2025-26",
        max_rename_fraction=0.9,
    )

    assert summary["aborted"] is None
    assert summary["emitted"] == 1


async def test_dry_run_posts_nothing(db_session, usa_wa):
    await _add_committee(db_session, source_id="200", name="New Name", anchor=ULID())
    wsl = _FakeWSL(
        _rosters(prior=[_committee(200, "Old Name")], current=[_committee(200, "New Name")])
    )
    pm = _FakePM()

    summary = await reconcile_committee_names(
        db_session, OrganizationDescriptor(), wsl, pm, biennium="2025-26", dry_run=True
    )

    assert pm.posted == []
    assert summary["dry_run"] is True
    assert summary["renamed"] == 1
    assert summary["emitted"] == 0


# --- per-row failure isolation ------------------------------------------------


async def test_rejected_disposition_is_counted(db_session, usa_wa):
    await _add_committee(db_session, source_id="1", name="New1", anchor=ULID())
    await _add_committee(db_session, source_id="2", name="New2", anchor=ULID())
    wsl = _FakeWSL(
        _rosters(
            prior=[_committee(1, "Old1"), _committee(2, "Old2")],
            current=[_committee(1, "New1"), _committee(2, "New2")],
        )
    )
    pm = _FakePM(lambda _p: ObservationResult(disposition=DISPOSITION_REJECTED, pm_id=None, raw={}))

    summary = await reconcile_committee_names(
        db_session, OrganizationDescriptor(), wsl, pm, biennium="2025-26", max_rename_fraction=1.0
    )

    assert summary["emitted"] == 0
    assert summary["rejected"] == 2


async def test_transient_failure_is_isolated(db_session, usa_wa):
    await _add_committee(db_session, source_id="1", name="New1", anchor=ULID())
    await _add_committee(db_session, source_id="2", name="New2", anchor=ULID())
    wsl = _FakeWSL(
        _rosters(
            prior=[_committee(1, "Old1"), _committee(2, "Old2")],
            current=[_committee(1, "New1"), _committee(2, "New2")],
        )
    )
    calls = {"n": 0}

    def _flaky(_payload):
        calls["n"] += 1
        if calls["n"] == 1:
            return RetryableClientError("PM 503")
        return ObservationResult(disposition=DISPOSITION_AUTO_ATTACHED, pm_id=ULID(), raw={})

    pm = _FakePM(_flaky)
    summary = await reconcile_committee_names(
        db_session, OrganizationDescriptor(), wsl, pm, biennium="2025-26", max_rename_fraction=1.0
    )

    assert summary["failed"] == 1
    assert summary["emitted"] == 1


async def test_payload_rejection_exception_is_isolated(db_session, usa_wa):
    await _add_committee(db_session, source_id="200", name="New Name", anchor=ULID())
    wsl = _FakeWSL(
        _rosters(prior=[_committee(200, "Old Name")], current=[_committee(200, "New Name")])
    )
    pm = _FakePM(lambda _p: PayloadRejectedError("422"))

    summary = await reconcile_committee_names(
        db_session, OrganizationDescriptor(), wsl, pm, biennium="2025-26", max_rename_fraction=1.0
    )

    assert summary["rejected"] == 1
    assert summary["emitted"] == 0


async def test_auth_block_aborts_run(db_session, usa_wa):
    await _add_committee(db_session, source_id="200", name="New Name", anchor=ULID())
    wsl = _FakeWSL(
        _rosters(prior=[_committee(200, "Old Name")], current=[_committee(200, "New Name")])
    )
    pm = _FakePM(lambda _p: DeliveryBlockedError("403"))

    with pytest.raises(DeliveryBlockedError):
        await reconcile_committee_names(
            db_session,
            OrganizationDescriptor(),
            wsl,
            pm,
            biennium="2025-26",
            max_rename_fraction=1.0,
        )
