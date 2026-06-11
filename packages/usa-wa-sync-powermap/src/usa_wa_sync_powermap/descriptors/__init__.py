"""Concrete EntityDescriptors for usa-wa's PM sync."""

from usa_wa_sync_powermap.descriptors.assignment import AssignmentDescriptor
from usa_wa_sync_powermap.descriptors.jurisdiction import JurisdictionDescriptor
from usa_wa_sync_powermap.descriptors.organization import OrganizationDescriptor
from usa_wa_sync_powermap.descriptors.person import PersonDescriptor
from usa_wa_sync_powermap.descriptors.role import RoleDescriptor

__all__ = [
    "AssignmentDescriptor",
    "JurisdictionDescriptor",
    "OrganizationDescriptor",
    "PersonDescriptor",
    "RoleDescriptor",
]
