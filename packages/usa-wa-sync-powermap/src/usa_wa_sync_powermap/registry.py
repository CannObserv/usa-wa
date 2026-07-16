"""The usa-wa descriptor registry — what the sidecar syncs.

The full identity cluster: jurisdictions + the four producer entities
(organizations, roles, persons, assignments). Entity events are NOT here — they
are a person/org sub-resource.
"""

from clearinghouse_sync_powermap.client import PowerMapClient
from clearinghouse_sync_powermap.descriptors import EntityDescriptor
from clearinghouse_sync_powermap.engine import SyncEngine
from clearinghouse_sync_powermap.pmclient import GeneratedPowerMapClient
from clearinghouse_sync_powermap.subscriptions import DiscoverySpec, SubscriptionReconciler
from usa_wa_sync_powermap.config import SidecarSettings
from usa_wa_sync_powermap.descriptors import (
    AssignmentDescriptor,
    JurisdictionDescriptor,
    OrganizationDescriptor,
    PersonDescriptor,
    RoleDescriptor,
)


def build_pm_client(settings: SidecarSettings) -> GeneratedPowerMapClient:
    """The one way production code constructs the PM client (#85).

    Centralizes the pacing wiring: every caller — sidecar daemon, bootstrap, the
    reconcile/validate/heal CLIs — gets ``powermap_min_request_interval`` applied,
    so no code path can burst PM's rate limit by forgetting the knob.
    """
    return GeneratedPowerMapClient(
        settings.powermap_base_url,
        settings.powermap_api_key,
        min_request_interval=settings.powermap_min_request_interval,
    )


def build_descriptors(settings: SidecarSettings | None = None) -> list[EntityDescriptor]:
    """Construct the descriptor set the sidecar engine operates over.

    Order is dependency-first: jurisdictions → the org tree they govern → roles
    within those orgs → persons → assignments (which bind a person to a role).
    This order is **load-bearing** in two ways. (1) At *delivery* time each
    descriptor's ``dependencies_ready`` gate defers a role/assignment whose
    parents aren't yet anchored (deferred, not failed). (2) The engine reads this
    list index as the outbox **drain priority** (``_drain_priority``): a
    dependency root (org/role) is drained before its dependents (assignments)
    inside one batch, so a flood of dependency-blocked dependents can't starve a
    root out of the ``next_attempt_at``-ordered ``LIMIT`` cut (usa-wa#96).
    Reordering this list changes both behaviours — keep it topological.

    ``settings`` (#12): when provided and ``powermap_search_match_cap`` is set, the
    org/person match-cascade name-search cap is overridden; ``None`` (or an unset
    cap) keeps each descriptor's historical per-entity default — non-breaking.

    ``settings.reconcile_cadence`` (#73 Axis 2): when ``settings`` is provided, the
    reconcile-backstop cadence is retuned on every producer that runs a backstop
    (``reconcile_enabled`` — jurisdictions are inert, ``reconcile_mode == "none"``). The
    ``reconcile_enabled`` predicate matches the one the subscription reconciler uses to
    pick the local-cohort producers, so the two features stay in lockstep if a future
    producer runs a different backstop mode. ``None`` settings keeps the base 1h default
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
            if descriptor.reconcile_enabled:
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
