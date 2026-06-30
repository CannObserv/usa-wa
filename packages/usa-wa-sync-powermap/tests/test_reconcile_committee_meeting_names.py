"""Producer-side Joint/`Other` committee **rename** detection — meeting-derived (#56).

Sibling of #46 for the class ``CommitteeService.GetCommittees`` can't see (#39). The rename
signal is the same — stable WSL ``Id``, changed name across a biennium boundary — but the
cohort is diffed from two bienniums' ``GetCommitteeMeetings`` windows, and three things
differ from #46:

1. **Source** — a meeting-derived ``{Id: name}`` cohort (via the provider), ``org_type='other'``.
2. **Clean name emitted** — the cohort name is WSL's clean ``Name`` (#61 ``observed_name``),
   never the agency-double-prefixed ``LongName`` stored as ``Organization.name``; the same
   clean string is diffed and emitted.
3. **Relaxed guards** — the low-overlap guard is off by default (dormancy-prone cohorts
   overlap thinly), and the rename-storm fraction only applies once the overlap is large
   enough to be meaningful.

Shared spine (diff, eligibility, per-row failure isolation, emit-to-PM-only) is exercised by
the #46 suite; here we pin the meeting-cohort sourcing, the clean-name emit, and the
re-tuned guards.
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
from usa_wa_sync_powermap.reconcile_committee_meeting_names import (
    reconcile_committee_meeting_names,
)

BOUNDARY = date(2025, 1, 1)  # biennium_start_date("2025-26")

# ESEC (Id 13945) — the live rename instance from #56. Clean names (what PM receives), and
# the local row's double-prefixed LongName (what must NOT reach PM).
_ESEC_OLD = "Joint Committee on Energy Supply & Energy Conservation"
_ESEC_NEW = "Joint Committee on Energy Supply, Energy Conservation, and Energy Resilience"
_ESEC_LONGNAME = f"Joint {_ESEC_NEW}"  # the "Joint Joint …" form stored as Organization.name


class _FakeCohorts:
    """Stub meeting-cohort provider — maps each biennium to its fixed ``{id: name}``."""

    def __init__(self, cohorts):
        self._cohorts = cohorts  # {biennium: {source_id: name}}
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


async def _add_other(
    session,
    *,
    source_id,
    name="Joint Joint Committee",
    short_name="Joint Committee",
    anchor=None,
    source="usa_wa_legislature",
    org_type="other",
):
    row = Organization(
        source=source,
        source_id=source_id,
        name=name,
        short_name=short_name,
        org_type=org_type,
        pm_organization_id=anchor,
    )
    session.add(row)
    await session.flush()
    return row


def _cohorts(*, prior, current):
    return {"2023-24": dict(prior), "2025-26": dict(current)}


# --- meeting-cohort sourcing + clean-name emit --------------------------------


async def test_renamed_joint_committee_emits_clean_windowed_name_pair(db_session, usa_wa):
    """Stable Id + changed clean Name across the boundary → one observation keyed by the PM
    anchor carrying both windowed CLEAN name rows — never the double-prefixed LongName."""
    anchor = ULID()
    await _add_other(
        db_session,
        source_id="13945",
        name=_ESEC_LONGNAME,  # double-prefixed LongName stored locally
        short_name=_ESEC_NEW,
        anchor=anchor,
    )
    provider = _FakeCohorts(_cohorts(prior={"13945": _ESEC_OLD}, current={"13945": _ESEC_NEW}))
    pm = _FakePM()

    summary = await reconcile_committee_meeting_names(
        db_session, OrganizationDescriptor(), provider, pm, biennium="2025-26"
    )

    assert provider.calls == ["2025-26", "2023-24"]  # current then prior
    assert len(pm.posted) == 1
    _, payload = pm.posted[0]
    assert payload["identifier_value"] == str(anchor)
    names = {n["name"]: n for n in payload["names"]}
    # CLEAN names (no "Joint Joint" double-prefix), mirroring #61 observed_name.
    assert _ESEC_OLD in names
    assert _ESEC_NEW in names
    assert not any(n.startswith("Joint Joint") for n in names)
    assert names[_ESEC_OLD]["name_type"] == "former"  # #58: prior name designated former
    assert names[_ESEC_OLD]["effective_end"] == BOUNDARY.isoformat()
    assert names[_ESEC_NEW]["name_type"] == "legal"  # current name stays legal
    assert summary["renamed"] == 1
    assert summary["emitted"] == 1
    assert summary["aborted"] is None


async def test_same_clean_name_emits_nothing(db_session, usa_wa):
    await _add_other(db_session, source_id="-140", anchor=ULID())
    provider = _FakeCohorts(
        _cohorts(
            prior={"-140": "Joint Transportation Committee"},
            current={"-140": "Joint Transportation Committee"},
        )
    )
    pm = _FakePM()

    summary = await reconcile_committee_meeting_names(
        db_session, OrganizationDescriptor(), provider, pm, biennium="2025-26"
    )

    assert pm.posted == []
    assert summary["renamed"] == 0


async def test_dormant_body_present_in_one_window_is_not_a_rename(db_session, usa_wa):
    """A body absent from one biennium's meeting window (dormancy) is only on one side of the
    diff → never a rename, even though the other overlapping body was renamed."""
    await _add_other(db_session, source_id="-140", anchor=ULID())
    await _add_other(db_session, source_id="-5", anchor=ULID())
    provider = _FakeCohorts(
        _cohorts(
            prior={"-140": "Joint Transportation Committee", "-5": "JLARC"},
            current={"-140": "Joint Transportation Cmte"},  # -5 dormant this biennium
        )
    )
    pm = _FakePM()

    summary = await reconcile_committee_meeting_names(
        db_session,
        OrganizationDescriptor(),
        provider,
        pm,
        biennium="2025-26",
        max_rename_fraction=1.0,
    )

    assert summary["overlap"] == 1  # only -140 in both windows
    assert summary["renamed"] == 1
    assert summary["emitted"] == 1


async def test_committee_class_row_is_out_of_scope(db_session, usa_wa):
    """A renamed Id whose local row is the org_type='committee' class is not governed here —
    #56 owns only org_type='other'. Not in the 'other' produced set → unproduced."""
    await _add_other(db_session, source_id="-140", anchor=ULID(), org_type="committee")
    provider = _FakeCohorts(
        _cohorts(prior={"-140": "Old Joint Name"}, current={"-140": "New Joint Name"})
    )
    pm = _FakePM()

    summary = await reconcile_committee_meeting_names(
        db_session,
        OrganizationDescriptor(),
        provider,
        pm,
        biennium="2025-26",
        max_rename_fraction=1.0,
    )

    assert pm.posted == []
    assert summary["skipped_unproduced"] == 1
    assert summary["aborted"] is None


# --- per-row eligibility ------------------------------------------------------


async def test_unanchored_renamed_is_skipped_counted(db_session, usa_wa):
    await _add_other(db_session, source_id="-140", anchor=None)
    provider = _FakeCohorts(
        _cohorts(prior={"-140": "Old Joint Name"}, current={"-140": "New Joint Name"})
    )
    pm = _FakePM()

    summary = await reconcile_committee_meeting_names(
        db_session,
        OrganizationDescriptor(),
        provider,
        pm,
        biennium="2025-26",
        max_rename_fraction=1.0,
    )

    assert pm.posted == []
    assert summary["skipped_unanchored"] == 1


async def test_archived_renamed_is_counted_hidden(db_session, usa_wa):
    row = await _add_other(db_session, source_id="-140", anchor=ULID())
    row.archived_at = datetime.now(UTC)
    await db_session.flush()
    provider = _FakeCohorts(
        _cohorts(prior={"-140": "Old Joint Name"}, current={"-140": "New Joint Name"})
    )
    pm = _FakePM()

    summary = await reconcile_committee_meeting_names(
        db_session,
        OrganizationDescriptor(),
        provider,
        pm,
        biennium="2025-26",
        max_rename_fraction=1.0,
    )

    assert pm.posted == []
    assert summary["skipped_hidden"] == 1
    assert summary["skipped_unproduced"] == 0


# --- re-tuned guardrails ------------------------------------------------------


async def test_empty_current_pull_aborts(db_session, usa_wa):
    await _add_other(db_session, source_id="-140", anchor=ULID())
    provider = _FakeCohorts(_cohorts(prior={"-140": "Old Joint Name"}, current={}))
    pm = _FakePM()

    summary = await reconcile_committee_meeting_names(
        db_session, OrganizationDescriptor(), provider, pm, biennium="2025-26"
    )

    assert summary["aborted"] == "empty_pull"


async def test_dormancy_thinned_overlap_passes_the_relaxed_floor(db_session, usa_wa):
    """A dormancy-thinned overlap (0.25 of the smaller cohort) clears the relaxed low-non-zero
    floor (0.1) where #46's 0.5 floor would abort — thin overlap is normal for this class."""
    await _add_other(db_session, source_id="-140", anchor=ULID())
    provider = _FakeCohorts(
        _cohorts(
            # overlap = {-140}; against the smaller cohort (4) that is 0.25 ≥ 0.1 floor.
            prior={"-140": "Old Joint Name", "91": "A", "92": "B", "93": "C"},
            current={"-140": "New Joint Name", "94": "D", "95": "E", "96": "F"},
        )
    )
    pm = _FakePM()

    summary = await reconcile_committee_meeting_names(
        db_session,
        OrganizationDescriptor(),
        provider,
        pm,
        biennium="2025-26",
        max_rename_fraction=1.0,
    )

    assert summary["aborted"] is None
    assert summary["overlap"] == 1
    assert summary["emitted"] == 1


async def test_near_disjoint_pull_still_aborts_low_overlap(db_session, usa_wa):
    """The relaxed floor is **low non-zero**, not off: a badly-wrong-biennium pull that shares
    almost nothing (1 of 12 = 0.083 < 0.1) still aborts rather than reading as a clean
    "renamed: 0" — the silent-miss #46's guard exists to prevent, kept for this class too."""
    await _add_other(db_session, source_id="-140", anchor=ULID())
    provider = _FakeCohorts(
        _cohorts(
            prior={"-140": "Old", **{str(i): f"P{i}" for i in range(90, 101)}},  # 12
            current={"-140": "New", **{str(i): f"C{i}" for i in range(70, 81)}},  # 12, shares -140
        )
    )
    pm = _FakePM()

    summary = await reconcile_committee_meeting_names(
        db_session, OrganizationDescriptor(), provider, pm, biennium="2025-26"
    )

    assert summary["aborted"] == "low_overlap"
    assert summary["emitted"] == 0


async def test_small_overlap_does_not_trip_storm_floor(db_session, usa_wa):
    """A tiny overlap where every body renamed is NOT a storm — the fraction is meaningless
    below storm_floor_min_overlap, so it must not abort (the #46 hair-trigger fixed)."""
    await _add_other(db_session, source_id="-140", anchor=ULID())
    await _add_other(db_session, source_id="-5", anchor=ULID())
    provider = _FakeCohorts(
        _cohorts(
            prior={"-140": "Old A", "-5": "Old B"},
            current={"-140": "New A", "-5": "New B"},
        )
    )  # overlap 2, 2 renamed → fraction 1.0, but overlap < floor (5)
    pm = _FakePM()

    summary = await reconcile_committee_meeting_names(
        db_session, OrganizationDescriptor(), provider, pm, biennium="2025-26"
    )

    assert summary["aborted"] is None
    assert summary["emitted"] == 2


async def test_storm_floor_aborts_once_overlap_is_large(db_session, usa_wa):
    """Past the floor, a renamed fraction over the default still aborts as a normalisation/
    wrong-biennium artifact."""
    ids = [str(-i) for i in range(1, 7)]  # 6 bodies, all renamed → fraction 1.0 ≥ floor
    for sid in ids:
        await _add_other(db_session, source_id=sid, anchor=ULID())
    provider = _FakeCohorts(
        _cohorts(
            prior={sid: f"Old {sid}" for sid in ids},
            current={sid: f"New {sid}" for sid in ids},
        )
    )
    pm = _FakePM()

    summary = await reconcile_committee_meeting_names(
        db_session, OrganizationDescriptor(), provider, pm, biennium="2025-26"
    )

    assert summary["aborted"] == "rename_storm"
    assert summary["emitted"] == 0


async def test_dry_run_posts_nothing(db_session, usa_wa):
    await _add_other(db_session, source_id="-140", anchor=ULID())
    provider = _FakeCohorts(
        _cohorts(prior={"-140": "Old Joint Name"}, current={"-140": "New Joint Name"})
    )
    pm = _FakePM()

    summary = await reconcile_committee_meeting_names(
        db_session,
        OrganizationDescriptor(),
        provider,
        pm,
        biennium="2025-26",
        dry_run=True,
    )

    assert pm.posted == []
    assert summary["dry_run"] is True
    assert summary["renamed"] == 1
    assert summary["emitted"] == 0


# --- per-row failure isolation ------------------------------------------------


async def test_rejected_disposition_is_counted(db_session, usa_wa):
    await _add_other(db_session, source_id="-140", anchor=ULID())
    provider = _FakeCohorts(
        _cohorts(prior={"-140": "Old Joint Name"}, current={"-140": "New Joint Name"})
    )
    pm = _FakePM(lambda _p: ObservationResult(disposition=DISPOSITION_REJECTED, pm_id=None, raw={}))

    summary = await reconcile_committee_meeting_names(
        db_session,
        OrganizationDescriptor(),
        provider,
        pm,
        biennium="2025-26",
        max_rename_fraction=1.0,
    )

    assert summary["emitted"] == 0
    assert summary["rejected"] == 1


async def test_anchoring_disposition_without_pm_id_counts_failed(db_session, usa_wa):
    """An anchoring disposition that returns no pm_id is neither anchored nor rejected — it
    falls through to ``failed`` (the spine's else branch), not silently counted as emitted."""
    await _add_other(db_session, source_id="-140", anchor=ULID())
    provider = _FakeCohorts(
        _cohorts(prior={"-140": "Old Joint Name"}, current={"-140": "New Joint Name"})
    )
    pm = _FakePM(
        lambda _p: ObservationResult(disposition=DISPOSITION_AUTO_ATTACHED, pm_id=None, raw={})
    )

    summary = await reconcile_committee_meeting_names(
        db_session,
        OrganizationDescriptor(),
        provider,
        pm,
        biennium="2025-26",
        max_rename_fraction=1.0,
    )

    assert summary["emitted"] == 0
    assert summary["failed"] == 1


async def test_transient_failure_is_isolated(db_session, usa_wa):
    await _add_other(db_session, source_id="-140", anchor=ULID())
    provider = _FakeCohorts(
        _cohorts(prior={"-140": "Old Joint Name"}, current={"-140": "New Joint Name"})
    )
    pm = _FakePM(lambda _p: RetryableClientError("PM 503"))

    summary = await reconcile_committee_meeting_names(
        db_session,
        OrganizationDescriptor(),
        provider,
        pm,
        biennium="2025-26",
        max_rename_fraction=1.0,
    )

    assert summary["failed"] == 1
    assert summary["emitted"] == 0


async def test_payload_rejection_exception_is_isolated(db_session, usa_wa):
    await _add_other(db_session, source_id="-140", anchor=ULID())
    provider = _FakeCohorts(
        _cohorts(prior={"-140": "Old Joint Name"}, current={"-140": "New Joint Name"})
    )
    pm = _FakePM(lambda _p: PayloadRejectedError("422"))

    summary = await reconcile_committee_meeting_names(
        db_session,
        OrganizationDescriptor(),
        provider,
        pm,
        biennium="2025-26",
        max_rename_fraction=1.0,
    )

    assert summary["rejected"] == 1


async def test_auth_block_aborts_run(db_session, usa_wa):
    await _add_other(db_session, source_id="-140", anchor=ULID())
    provider = _FakeCohorts(
        _cohorts(prior={"-140": "Old Joint Name"}, current={"-140": "New Joint Name"})
    )
    pm = _FakePM(lambda _p: DeliveryBlockedError("403"))

    with pytest.raises(DeliveryBlockedError):
        await reconcile_committee_meeting_names(
            db_session,
            OrganizationDescriptor(),
            provider,
            pm,
            biennium="2025-26",
            max_rename_fraction=1.0,
        )
