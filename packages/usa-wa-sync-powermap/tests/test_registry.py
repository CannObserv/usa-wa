"""Registry tests — the descriptor set and the discovery spec the sidecar uses."""

from datetime import timedelta

import pytest

from usa_wa_sync_powermap import bootstrap
from usa_wa_sync_powermap.config import SidecarSettings
from usa_wa_sync_powermap.registry import build_descriptors, build_discovery_spec


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
    assert spec.follow == [
        "lineage",
        "affiliated_orgs",
        "org_children",
        "roles",
        "assignments",
        "people",
    ]


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
