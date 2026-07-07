"""Registry tests — the descriptor set and the discovery spec the sidecar uses."""

from datetime import timedelta

import pytest

from clearinghouse_sync_powermap.engine import SyncEngine
from clearinghouse_sync_powermap.testing import FakeClient
from usa_wa_sync_powermap import bootstrap
from usa_wa_sync_powermap.config import SidecarSettings
from usa_wa_sync_powermap.registry import (
    build_descriptors,
    build_discovery_spec,
    build_reconciler,
)


def test_build_descriptors_covers_identity_cluster():
    types = {d.entity_type for d in build_descriptors()}
    assert types == {"jurisdiction", "organization", "role", "role_assignment", "person"}


def test_no_descriptor_runs_full_list_reconcile():
    # usa-wa#10: the unfiltered full-list reconcile is retired across the board. The
    # producers run the bounded anchored-cohort backstop (#13); jurisdictions run none.
    modes = {d.entity_type: d.reconcile_mode for d in build_descriptors()}
    assert all(m != "full_list" for m in modes.values())
    assert modes["jurisdiction"] == "none"
    assert modes["organization"] == "anchored_cohort"
    assert modes["role"] == "anchored_cohort"
    assert modes["role_assignment"] == "anchored_cohort"
    assert modes["person"] == "anchored_cohort"


def test_search_match_cap_defaults_preserve_current_behavior():
    # #12: the cap is configurable but defaults to today's effective values so the
    # change is non-breaking (orgs 50, people 20 — the prior _SEARCH_LIMIT constants).
    by_type = {d.entity_type: d for d in build_descriptors()}
    assert by_type["organization"].search_match_cap == 50
    assert by_type["person"].search_match_cap == 20


def test_build_descriptors_plumbs_configured_search_cap():
    # #12: SidecarSettings.powermap_search_match_cap flows to the match-cascade
    # descriptors so an operator can widen the candidate window without a code change.
    settings = SidecarSettings(powermap_api_key="x", powermap_search_match_cap=200)
    by_type = {d.entity_type: d for d in build_descriptors(settings)}
    assert by_type["organization"].search_match_cap == 200
    assert by_type["person"].search_match_cap == 200


def test_reconcile_cadence_setting_defaults_to_twelve_hours():
    # #73 Axis 2: the anchored-cohort backstop is a dropped-feed-event safety net for a
    # low-churn dataset, not the primary path — a twice-daily re-fetch of OUR cohort
    # (each person also pulling /events) is ample. Longer than the 1h base default.
    assert SidecarSettings(powermap_api_key="x").reconcile_cadence == timedelta(hours=12)


def test_subscription_backstop_cadence_defaults_to_six_hours():
    # #73 Axis 2: graph drift is slow (new WA committees enter via the daily WSL
    # refresh), so the hourly full-subtree re-discovery walk is wasteful. Six-hourly
    # still catches a newly-added committee several times a day.
    assert SidecarSettings(powermap_api_key="x").subscription_backstop_cadence == timedelta(hours=6)


def test_configured_reconcile_cadence_flows_to_anchored_cohort_descriptors():
    # #73 Axis 2: SidecarSettings.reconcile_cadence overrides the per-descriptor
    # backstop cadence on the producers that run it, so an operator can retune the
    # people-call volume without a code change. Jurisdictions (mode "none") are inert.
    settings = SidecarSettings(powermap_api_key="x", reconcile_cadence=timedelta(hours=8))
    by_type = {d.entity_type: d for d in build_descriptors(settings)}
    for entity_type in ("organization", "role", "role_assignment", "person"):
        assert by_type[entity_type].reconcile_cadence == timedelta(hours=8)


def test_build_descriptors_without_settings_keeps_base_cadence():
    # Mirrors the search-cap contract: no settings → each descriptor's historical
    # base default (1h), so a bare build_descriptors() call is non-breaking.
    by_type = {d.entity_type: d for d in build_descriptors()}
    assert by_type["organization"].reconcile_cadence == timedelta(hours=1)


def test_build_discovery_spec_roots_at_wa_subtree():
    spec = build_discovery_spec(SidecarSettings(powermap_api_key="x"))
    assert spec.root_type == "jurisdiction"
    assert spec.root_id == "usa-wa"
    # #73 Axis 1: PM discovery is narrowed to the jurisdiction lineage (the mirror-only,
    # PM-authoritative cache). The producer subtree edges (affiliated_orgs/org_children/
    # roles/assignments/people) are dropped — our produced rows are subscribed from the
    # local anchored cohort instead, so PM discovery no longer drags in strangers.
    assert spec.follow == ["lineage"]


def test_discovery_follow_defaults_to_lineage_only():
    # #73 Axis 1: the default follow set is jurisdiction lineage only; env can override.
    assert SidecarSettings(powermap_api_key="x").powermap_discovery_follow == ["lineage"]


def test_build_reconciler_enables_local_cohort():
    # #73 Axis 1: the usa-wa reconciler subscribes our locally-anchored producer rows
    # (not the whole PM subtree), so include_local_cohort must be wired on.
    settings = SidecarSettings(powermap_api_key="x")
    descriptors = build_descriptors(settings)
    client = FakeClient(discovered=[], subscribed=[])
    engine = SyncEngine(descriptors, client)
    reconciler = build_reconciler(client, engine, settings)
    assert reconciler.include_local_cohort is True
    assert reconciler._spec.follow == ["lineage"]


def test_bootstrap_entrypoint_is_callable():
    # The deploy/cutover step shells out to this; keep it importable + wired.
    assert callable(bootstrap.main)


class _Stop(Exception):
    """Sentinel to abort an entrypoint right after build_descriptors runs."""


async def _assert_entrypoint_passes_settings(monkeypatch, module):
    """#12: the daemon entrypoints must pass ``settings`` to build_descriptors so the
    configured ``powermap_search_match_cap`` actually reaches the descriptors —
    calling it with no args leaves the knob inert. Capture the arg, then abort before
    any network/DB work."""
    captured = {}

    def _fake_build(settings=None):
        captured["settings"] = settings
        raise _Stop

    monkeypatch.setattr(module, "build_descriptors", _fake_build)
    monkeypatch.setattr(
        module,
        "get_sidecar_settings",
        lambda: SidecarSettings(powermap_api_key="x", powermap_search_match_cap=123),
    )
    with pytest.raises(_Stop):
        await module._amain()
    assert captured["settings"] is not None
    assert captured["settings"].powermap_search_match_cap == 123


async def test_bootstrap_passes_settings_to_build_descriptors(monkeypatch):
    await _assert_entrypoint_passes_settings(monkeypatch, bootstrap)


async def test_daemon_main_passes_settings_to_build_descriptors(monkeypatch):
    from usa_wa_sync_powermap import __main__ as daemon

    await _assert_entrypoint_passes_settings(monkeypatch, daemon)
