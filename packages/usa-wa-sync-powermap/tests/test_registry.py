"""Registry tests — the descriptor set and the discovery spec the sidecar uses."""

from usa_wa_sync_powermap import bootstrap
from usa_wa_sync_powermap.config import SidecarSettings
from usa_wa_sync_powermap.registry import build_descriptors, build_discovery_spec


def test_build_descriptors_covers_identity_cluster():
    types = {d.entity_type for d in build_descriptors()}
    assert types == {"jurisdiction", "organization", "role", "role_assignment", "person"}


def test_no_descriptor_runs_full_list_reconcile():
    # usa-wa#10: the unfiltered full-list reconcile is retired across the board.
    assert all(not d.reconcile_enabled for d in build_descriptors())


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
