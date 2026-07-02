"""Read-only local↔PM committee validation (sub-project 1).

Two layers under test:

* the **pure classifier** (`classify_org` / `pm_org_from_record`) — no DB, no PM;
  one case per discrepancy bucket plus clean/reconciled/multi-issue, and the
  PM-record → snapshot mapping;
* the **orchestrator + CLI** (`validate_committees`, `_fetch_pm`, `_run`, `main`)
  — cohort load from the DB, the bounded `RetryableClientError` backoff, the
  empty-cohort abort, and `main`'s exit-code contract (0 clean / 1 divergent /
  2 auth / 3 abort).
"""

import json
from contextlib import asynccontextmanager
from datetime import date
from types import SimpleNamespace

import pytest
from ulid import ULID

from clearinghouse_domain_legislative.identity import (
    Organization,
)
from clearinghouse_sync_powermap.client import DeliveryBlockedError, RetryableClientError
from usa_wa_sync_powermap import validate_committees as vc

# --- pure classifier: snapshot builders --------------------------------------


def _local(**over):
    base = dict(
        source_id="100",
        name="Joint Transportation Committee",
        short_name="Joint Transportation Committee",
        acronym=None,
        org_type="other",
        pm_organization_id="PM100",
        parent_pm_id="PMLEG",
        name_windows=(),
        acronym_variants=(),
    )
    base.update(over)
    return vc.LocalOrg(**base)


def _pm(**over):
    base = dict(
        pm_id="PM100",
        name="Joint Transportation Committee",
        parent_id="PMLEG",
        names=(),
        acronyms=(),
    )
    base.update(over)
    return vc.PMOrg(**base)


def _nw(**over):
    base = dict(
        name="Joint Transportation Committee",
        name_type="legal",
        is_canonical=True,
        effective_start=None,
        effective_end=None,
        pm_org_name_id="N1",
    )
    base.update(over)
    return vc.NameWindow(**base)


def _av(**over):
    base = dict(acronym="JTC", is_canonical=True, pm_org_acronym_id="A1")
    base.update(over)
    return vc.AcronymVariant(**base)


# --- pure classifier: one case per bucket ------------------------------------


def test_clean_when_local_matches_pm():
    report = vc.classify_org(_local(), _pm())
    assert report.issues == ()
    assert report.divergent is False
    assert report.reconciled is False


def test_unlinked_when_no_pm_id():
    report = vc.classify_org(_local(pm_organization_id=None), None)
    assert report.issues == (vc.ISSUE_UNLINKED,)
    assert report.divergent is True


def test_missing_in_pm_on_404():
    report = vc.classify_org(_local(), None)
    assert report.issues == (vc.ISSUE_MISSING,)


def test_merged_when_pm_tombstone():
    report = vc.classify_org(_local(), vc.PMTombstone(merged_into="PM999"))
    assert report.issues == (vc.ISSUE_MERGED,)
    assert report.detail["merged_into"] == "PM999"


def test_name_drift_when_scalar_differs():
    # Local still holds the double-prefixed produced name; PM curated the clean one.
    report = vc.classify_org(
        _local(name="Joint Joint Transportation Committee"),
        _pm(name="Joint Transportation Committee"),
    )
    assert report.issues == (vc.ISSUE_NAME_DRIFT,)
    assert report.divergent is True


def test_acronym_drift_when_canonical_differs():
    report = vc.classify_org(
        _local(acronym="JTC"),
        _pm(acronyms=(_av(acronym="JLTC", is_canonical=True),)),
    )
    assert vc.ISSUE_ACRONYM_DRIFT in report.issues


def test_no_acronym_drift_when_pm_has_no_canonical():
    # PM carries the produced acronym but marks none is_canonical → nothing to adopt;
    # local retains its produced scalar, not a divergence (#64/#65).
    report = vc.classify_org(
        _local(acronym="CS"),
        _pm(acronyms=(_av(acronym="CS", is_canonical=False),)),
    )
    assert vc.ISSUE_ACRONYM_DRIFT not in report.issues


def test_acronym_drift_when_local_missing_pm_canonical():
    # Inverse: PM has a canonical, local scalar is empty → local should have adopted.
    report = vc.classify_org(
        _local(acronym=None),
        _pm(acronyms=(_av(acronym="WA CS", is_canonical=True),)),
    )
    assert vc.ISSUE_ACRONYM_DRIFT in report.issues


def test_names_window_drift_when_pm_window_unmirrored():
    # PM reports a former + legal window; local mirrored neither.
    report = vc.classify_org(
        _local(name_windows=()),
        _pm(names=(_nw(name_type="former", pm_org_name_id="N0"), _nw())),
    )
    assert vc.ISSUE_NAMES_WINDOW_DRIFT in report.issues


def test_acronyms_set_drift_when_variant_unmirrored():
    report = vc.classify_org(
        _local(acronym_variants=()),
        _pm(acronyms=(_av(),)),
    )
    assert vc.ISSUE_ACRONYMS_DRIFT in report.issues


def test_parent_drift_when_parent_differs():
    report = vc.classify_org(_local(parent_pm_id="PMX"), _pm(parent_id="PMLEG"))
    assert report.issues == (vc.ISSUE_PARENT_DRIFT,)


def test_reconciled_when_former_window_fully_mirrored():
    # PM curated a rename (former window); local mirrored the identical set → the
    # positive "PM-side change safely roundtripped" signal. No issues, reconciled.
    former = _nw(name="Old Name", name_type="former", pm_org_name_id="N0", is_canonical=False)
    legal = _nw()
    report = vc.classify_org(
        _local(name_windows=(former, legal)),
        _pm(names=(former, legal)),
    )
    assert report.issues == ()
    assert report.reconciled is True


def test_multiple_issues_accumulate():
    report = vc.classify_org(
        _local(name="X", parent_pm_id="PMX"),
        _pm(name="Y", parent_id="PMLEG"),
    )
    assert vc.ISSUE_NAME_DRIFT in report.issues
    assert vc.ISSUE_PARENT_DRIFT in report.issues


# --- pure classifier: PM record → snapshot -----------------------------------


def test_pm_org_from_record_maps_embedded_lists():
    record = {
        "id": "PM100",
        "name": "Joint Transportation Committee",
        "parent_id": "PMLEG",
        "names": [
            {
                "id": "N0",
                "name": "Old Name",
                "name_type": "former",
                "is_canonical": False,
                "effective_start": "2021-01-11",
                "effective_end": "2023-01-09",
            },
            {"id": "N1", "name": "Joint Transportation Committee", "is_canonical": True},
        ],
        "acronyms": [{"id": "A1", "acronym": "JTC", "is_canonical": True}],
    }
    pm = vc.pm_org_from_record(record)
    assert pm.pm_id == "PM100"
    assert pm.name == "Joint Transportation Committee"
    assert pm.parent_id == "PMLEG"
    assert {n.pm_org_name_id for n in pm.names} == {"N0", "N1"}
    former = next(n for n in pm.names if n.pm_org_name_id == "N0")
    assert former.name_type == "former"
    assert former.effective_start == date(2021, 1, 11)
    assert pm.acronyms[0].acronym == "JTC"


def test_pm_org_from_record_defaults_name_type_legal():
    pm = vc.pm_org_from_record({"id": "P", "name": "N", "names": [{"id": "N1", "name": "N"}]})
    assert pm.names[0].name_type == "legal"


# --- backoff wrapper ----------------------------------------------------------


async def test_fetch_pm_retries_then_succeeds():
    calls = {"n": 0}

    class _Client:
        async def get_entity(self, _path, _pm_id):
            calls["n"] += 1
            if calls["n"] == 1:
                raise RetryableClientError("PM 429")
            return {"id": "P", "name": "N"}

    slept = []

    async def _sleep(s):
        slept.append(s)

    got = await vc._fetch_pm(_Client(), vc.READ_PATH, "P", sleep=_sleep)
    assert got == {"id": "P", "name": "N"}
    assert calls["n"] == 2
    assert slept  # backed off once


async def test_fetch_pm_gives_up_after_max_attempts():
    class _Client:
        async def get_entity(self, _path, _pm_id):
            raise RetryableClientError("PM 503")

    async def _sleep(_s):
        pass

    with pytest.raises(RetryableClientError):
        await vc._fetch_pm(_Client(), vc.READ_PATH, "P", sleep=_sleep)


# --- orchestrator (DB cohort) -------------------------------------------------


async def _add_org(db_session, usa_wa, *, source_id, anchor, name="Org", org_type="committee"):
    row = Organization(
        source="usa_wa_legislature",
        source_id=source_id,
        name=name,
        short_name=name,
        org_type=org_type,
        pm_organization_id=anchor,
        jurisdiction_id=usa_wa.id,
    )
    db_session.add(row)
    await db_session.flush()
    return row


class _FakePM:
    """Returns a crafted OrgDetail dict per pm_id; None means 404."""

    def __init__(self, by_id):
        self._by_id = by_id
        self.closed = False

    async def get_entity(self, _path, pm_id):
        return self._by_id.get(str(pm_id))

    async def aclose(self):
        self.closed = True


async def test_validate_flags_divergent_and_counts_clean(db_session, usa_wa):
    a1, a2 = ULID(), ULID()
    await _add_org(db_session, usa_wa, source_id="100", anchor=a1, name="Clean Org")
    await _add_org(db_session, usa_wa, source_id="200", anchor=a2, name="Local Name")
    pm = _FakePM(
        {
            str(a1): {"id": str(a1), "name": "Clean Org"},
            str(a2): {"id": str(a2), "name": "PM Curated Name"},  # name drift
        }
    )
    summary = await vc.validate_committees(db_session, pm)
    assert summary["checked"] == 2
    assert summary["clean"] == 1
    assert summary["divergent"] == 1
    assert summary["by_issue"][vc.ISSUE_NAME_DRIFT] == 1
    assert summary["aborted"] is None


async def test_validate_reports_unlinked(db_session, usa_wa):
    await _add_org(db_session, usa_wa, source_id="100", anchor=None, name="Never synced")
    pm = _FakePM({})
    summary = await vc.validate_committees(db_session, pm)
    assert summary["by_issue"][vc.ISSUE_UNLINKED] == 1
    assert summary["divergent"] == 1


async def test_validate_empty_cohort_aborts(db_session, usa_wa):
    pm = _FakePM({})
    summary = await vc.validate_committees(db_session, pm)
    assert summary["aborted"] == "empty_cohort"
    assert summary["checked"] == 0


async def test_validate_includes_unbaselined_count(db_session, usa_wa):
    await _add_org(db_session, usa_wa, source_id="100", anchor=ULID(), name="Org")
    pm = _FakePM({})  # 404 → missing, irrelevant here
    summary = await vc.validate_committees(db_session, pm)
    assert "unbaselined_fetch_events" in summary


# --- main (exit codes) --------------------------------------------------------


def _patch_run(monkeypatch, result):
    async def _fake(_args):
        return result

    monkeypatch.setattr(vc, "_run", _fake)
    monkeypatch.setattr(vc, "configure_logging", lambda: None)


def test_main_clean_exits_zero(monkeypatch, capsys):
    _patch_run(monkeypatch, {"divergent": 0, "aborted": None, "checked": 5})
    assert vc.main([]) == 0
    assert json.loads(capsys.readouterr().out)["checked"] == 5


def test_main_divergent_exits_one(monkeypatch, capsys):
    _patch_run(monkeypatch, {"divergent": 2, "aborted": None, "checked": 5})
    assert vc.main([]) == 1


def test_main_abort_exits_three(monkeypatch, capsys):
    _patch_run(monkeypatch, {"divergent": 0, "aborted": "empty_cohort", "checked": 0})
    assert vc.main([]) == vc.EXIT_ABORTED == 3


def test_main_auth_block_exits_two(monkeypatch, capsys):
    async def _fake(_args):
        raise DeliveryBlockedError("PM 403")

    monkeypatch.setattr(vc, "_run", _fake)
    monkeypatch.setattr(vc, "configure_logging", lambda: None)
    assert vc.main([]) == 2
    assert json.loads(capsys.readouterr().out)["error"].startswith("delivery blocked")


async def test_run_requires_api_key(monkeypatch, db_session):
    """Read-only still needs PM creds to fetch — fail closed on an absent key."""

    @asynccontextmanager
    async def _ctx():
        yield db_session

    monkeypatch.setattr(vc, "get_session_factory", lambda: _ctx)
    monkeypatch.setattr(
        vc,
        "get_sidecar_settings",
        lambda: SimpleNamespace(powermap_api_key="", powermap_base_url="http://pm"),
    )
    args = vc._build_parser().parse_args([])
    with pytest.raises(RuntimeError, match="POWERMAP_API_KEY"):
        await vc._run(args)
