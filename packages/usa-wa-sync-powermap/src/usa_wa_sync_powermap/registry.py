"""The usa-wa descriptor registry — what the sidecar syncs.

The full identity cluster: jurisdictions + the four producer entities
(organizations, roles, persons, assignments). Entity events are NOT here — they
are a person/org sub-resource.
"""

from clearinghouse_sync_powermap.descriptors import EntityDescriptor
from usa_wa_sync_powermap.descriptors import (
    AssignmentDescriptor,
    JurisdictionDescriptor,
    OrganizationDescriptor,
    PersonDescriptor,
    RoleDescriptor,
)


def build_descriptors() -> list[EntityDescriptor]:
    """Construct the descriptor set the sidecar engine operates over.

    Order is informational (the engine indexes by ``entity_type``), but kept
    dependency-first: jurisdictions → the org tree they govern → roles within
    those orgs → persons → assignments (which bind a person to a role). Ordering
    at *delivery* time is enforced by each descriptor's ``dependencies_ready``
    gate, not by this list order — a role/assignment whose parents aren't yet
    anchored is deferred, not failed.
    """
    return [
        JurisdictionDescriptor(),
        OrganizationDescriptor(),
        RoleDescriptor(),
        PersonDescriptor(),
        AssignmentDescriptor(),
    ]
