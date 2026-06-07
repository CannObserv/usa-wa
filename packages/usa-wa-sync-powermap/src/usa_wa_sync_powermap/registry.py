"""The usa-wa descriptor registry — what the sidecar syncs.

Grows to the full 5 entities (persons/orgs/roles/assignments) as each descriptor
lands. Entity events are NOT here — they are a person/org sub-resource.
"""

from clearinghouse_sync_powermap.descriptors import EntityDescriptor
from usa_wa_sync_powermap.descriptors import JurisdictionDescriptor


def build_descriptors() -> list[EntityDescriptor]:
    """Construct the descriptor set the sidecar engine operates over."""
    return [JurisdictionDescriptor()]
