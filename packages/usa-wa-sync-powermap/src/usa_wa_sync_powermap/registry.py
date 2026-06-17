"""The usa-wa descriptor registry — what the sidecar syncs.

The full identity cluster: jurisdictions + the four producer entities
(organizations, roles, persons, assignments). Entity events are NOT here — they
are a person/org sub-resource.
"""

from clearinghouse_sync_powermap.descriptors import EntityDescriptor
from clearinghouse_sync_powermap.subscriptions import DiscoverySpec
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
    """
    match_cap = settings.powermap_search_match_cap if settings is not None else None
    return [
        JurisdictionDescriptor(),
        OrganizationDescriptor(search_match_cap=match_cap),
        RoleDescriptor(),
        PersonDescriptor(search_match_cap=match_cap),
        AssignmentDescriptor(),
    ]


def build_discovery_spec(settings: SidecarSettings) -> DiscoverySpec:
    """The WA-subtree discovery spec the reconciler traverses (PM #203).

    Rooted at the ``usa-wa`` jurisdiction, following lineage → governing orgs → org
    tree → roles → assignments → people, so the subscription set is exactly the WA
    identity cluster.
    """
    return DiscoverySpec(
        root_type=settings.powermap_discovery_root_type,
        root_id=settings.powermap_discovery_root_id,
        follow=settings.powermap_discovery_follow,
    )
