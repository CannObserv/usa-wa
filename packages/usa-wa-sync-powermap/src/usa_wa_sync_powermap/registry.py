"""The usa-wa descriptor registry — what the sidecar syncs.

The full identity cluster: jurisdictions + the four producer entities
(organizations, roles, persons, assignments). Entity events are NOT here — they
are a person/org sub-resource.
"""

from clearinghouse_sync_powermap.client import PowerMapClient
from clearinghouse_sync_powermap.descriptors import EntityDescriptor
from clearinghouse_sync_powermap.engine import SyncEngine
from clearinghouse_sync_powermap.subscriptions import DiscoverySpec, SubscriptionReconciler
from usa_wa_sync_powermap.config import SidecarSettings
from usa_wa_sync_powermap.descriptors import (
    AssignmentDescriptor,
    JurisdictionDescriptor,
    OrganizationDescriptor,
    PersonDescriptor,
    RoleDescriptor,
)


def build_descriptors(settings: SidecarSettings | None = None) -> list[EntityDescriptor]:
    """Construct the descriptor set the sidecar engine operates over.

    Order is informational (the engine indexes by ``entity_type``), but kept
    dependency-first: jurisdictions → the org tree they govern → roles within
    those orgs → persons → assignments (which bind a person to a role). Ordering
    at *delivery* time is enforced by each descriptor's ``dependencies_ready``
    gate, not by this list order — a role/assignment whose parents aren't yet
    anchored is deferred, not failed.

    ``settings`` (#12): when provided and ``powermap_search_match_cap`` is set, the
    org/person match-cascade name-search cap is overridden; ``None`` (or an unset
    cap) keeps each descriptor's historical per-entity default — non-breaking.

    ``settings.reconcile_cadence`` (#73 Axis 2): when ``settings`` is provided, the
    anchored-cohort backstop cadence is retuned on the producers that run it (jurisdictions
    are inert — ``reconcile_mode == "none"``). ``None`` settings keeps the base 1h default
    so a bare ``build_descriptors()`` call stays non-breaking (mirrors the search-cap contract).
    """
    match_cap = settings.powermap_search_match_cap if settings is not None else None
    descriptors: list[EntityDescriptor] = [
        JurisdictionDescriptor(),
        OrganizationDescriptor(search_match_cap=match_cap),
        RoleDescriptor(),
        PersonDescriptor(search_match_cap=match_cap),
        AssignmentDescriptor(),
    ]
    if settings is not None:
        for descriptor in descriptors:
            if descriptor.reconcile_mode == "anchored_cohort":
                descriptor.reconcile_cadence = settings.reconcile_cadence
    return descriptors


def build_discovery_spec(settings: SidecarSettings) -> DiscoverySpec:
    """The PM-discovery spec the reconciler traverses (PM #203).

    Rooted at the ``usa-wa`` jurisdiction, following its ``lineage`` only (#73 Axis 1) —
    the mirror-only, PM-authoritative jurisdiction cache usa-wa does not produce. The
    produced identity cluster (orgs/roles/persons/assignments) is subscribed from the
    local anchored cohort via ``build_reconciler``'s ``include_local_cohort``, not this
    subtree walk, so discovery no longer drags in PM-only strangers.
    """
    return DiscoverySpec(
        root_type=settings.powermap_discovery_root_type,
        root_id=settings.powermap_discovery_root_id,
        follow=settings.powermap_discovery_follow,
    )


def build_reconciler(
    client: PowerMapClient, engine: SyncEngine, settings: SidecarSettings
) -> SubscriptionReconciler:
    """The usa-wa subscription reconciler (#73 Axis 1).

    Wires ``include_local_cohort=True`` so the subscription set is (jurisdiction lineage
    via PM discovery) ∪ (OUR locally-anchored producer rows) — the mirror set — rather
    than the whole PM WA subtree. Shared by the bootstrap one-shot and the sidecar daemon
    so both agree on the membership policy.
    """
    return SubscriptionReconciler(
        client, engine, build_discovery_spec(settings), include_local_cohort=True
    )
