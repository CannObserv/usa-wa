"""The usa-wa descriptor registry — what the sidecar syncs.

Grows to the full 5 entities (persons/orgs/roles/assignments) as each descriptor
lands. Entity events are NOT here — they are a person/org sub-resource.
"""

from clearinghouse_sync_powermap.descriptors import EntityDescriptor
from usa_wa_sync_powermap.descriptors import (
    JurisdictionDescriptor,
    OrganizationDescriptor,
    RoleDescriptor,
)


def build_descriptors() -> list[EntityDescriptor]:
    """Construct the descriptor set the sidecar engine operates over.

    Order is informational (the engine indexes by ``entity_type``), but kept
    dependency-first: jurisdictions, then the org tree they govern, then the
    roles within those orgs. Assignments/persons follow once their descriptors
    land. Cross-entity ordering at *delivery* time is enforced by each
    descriptor's ``dependencies_ready`` gate, not by this list order.
    """
    return [JurisdictionDescriptor(), OrganizationDescriptor(), RoleDescriptor()]
