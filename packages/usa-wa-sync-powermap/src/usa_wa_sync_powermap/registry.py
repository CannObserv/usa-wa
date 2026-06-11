"""The usa-wa descriptor registry — what the sidecar syncs.

Grows to the full 5 entities (persons/orgs/roles/assignments) as each descriptor
lands. Entity events are NOT here — they are a person/org sub-resource.
"""

from clearinghouse_sync_powermap.descriptors import EntityDescriptor
from usa_wa_sync_powermap.descriptors import JurisdictionDescriptor, OrganizationDescriptor


def build_descriptors() -> list[EntityDescriptor]:
    """Construct the descriptor set the sidecar engine operates over.

    Order is informational (the engine indexes by ``entity_type``), but kept
    root-first: jurisdictions, then the org tree they govern. Roles/assignments
    follow once their descriptors land.
    """
    return [JurisdictionDescriptor(), OrganizationDescriptor()]
