from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.observation_additional_identifier import ObservationAdditionalIdentifier
    from ..models.observation_address import ObservationAddress
    from ..models.observation_contact_method import ObservationContactMethod
    from ..models.observation_event_item import ObservationEventItem
    from ..models.observation_link import ObservationLink
    from ..models.observation_name import ObservationName
    from ..models.observation_role_assignment import ObservationRoleAssignment


T = TypeVar("T", bound="PeopleObservationRequest")


@_attrs_define
class PeopleObservationRequest:
    """Payload for POST /api/v1/people/observations.

    Attributes:
        identifier_type (str):
        identifier_value (str):
        names (list[ObservationName] | Unset):
        personal_pronouns (None | str | Unset):
        role_assignments (list[ObservationRoleAssignment] | Unset):
        links (list[ObservationLink] | Unset):
        contact_methods (list[ObservationContactMethod] | Unset):
        addresses (list[ObservationAddress] | Unset):
        additional_identifiers (list[ObservationAdditionalIdentifier] | Unset):
        events (list[ObservationEventItem] | Unset):
    """

    identifier_type: str
    identifier_value: str
    names: list[ObservationName] | Unset = UNSET
    personal_pronouns: None | str | Unset = UNSET
    role_assignments: list[ObservationRoleAssignment] | Unset = UNSET
    links: list[ObservationLink] | Unset = UNSET
    contact_methods: list[ObservationContactMethod] | Unset = UNSET
    addresses: list[ObservationAddress] | Unset = UNSET
    additional_identifiers: list[ObservationAdditionalIdentifier] | Unset = UNSET
    events: list[ObservationEventItem] | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        identifier_type = self.identifier_type

        identifier_value = self.identifier_value

        names: list[dict[str, Any]] | Unset = UNSET
        if not isinstance(self.names, Unset):
            names = []
            for names_item_data in self.names:
                names_item = names_item_data.to_dict()
                names.append(names_item)

        personal_pronouns: None | str | Unset
        if isinstance(self.personal_pronouns, Unset):
            personal_pronouns = UNSET
        else:
            personal_pronouns = self.personal_pronouns

        role_assignments: list[dict[str, Any]] | Unset = UNSET
        if not isinstance(self.role_assignments, Unset):
            role_assignments = []
            for role_assignments_item_data in self.role_assignments:
                role_assignments_item = role_assignments_item_data.to_dict()
                role_assignments.append(role_assignments_item)

        links: list[dict[str, Any]] | Unset = UNSET
        if not isinstance(self.links, Unset):
            links = []
            for links_item_data in self.links:
                links_item = links_item_data.to_dict()
                links.append(links_item)

        contact_methods: list[dict[str, Any]] | Unset = UNSET
        if not isinstance(self.contact_methods, Unset):
            contact_methods = []
            for contact_methods_item_data in self.contact_methods:
                contact_methods_item = contact_methods_item_data.to_dict()
                contact_methods.append(contact_methods_item)

        addresses: list[dict[str, Any]] | Unset = UNSET
        if not isinstance(self.addresses, Unset):
            addresses = []
            for addresses_item_data in self.addresses:
                addresses_item = addresses_item_data.to_dict()
                addresses.append(addresses_item)

        additional_identifiers: list[dict[str, Any]] | Unset = UNSET
        if not isinstance(self.additional_identifiers, Unset):
            additional_identifiers = []
            for additional_identifiers_item_data in self.additional_identifiers:
                additional_identifiers_item = additional_identifiers_item_data.to_dict()
                additional_identifiers.append(additional_identifiers_item)

        events: list[dict[str, Any]] | Unset = UNSET
        if not isinstance(self.events, Unset):
            events = []
            for events_item_data in self.events:
                events_item = events_item_data.to_dict()
                events.append(events_item)

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "identifier_type": identifier_type,
                "identifier_value": identifier_value,
            }
        )
        if names is not UNSET:
            field_dict["names"] = names
        if personal_pronouns is not UNSET:
            field_dict["personal_pronouns"] = personal_pronouns
        if role_assignments is not UNSET:
            field_dict["role_assignments"] = role_assignments
        if links is not UNSET:
            field_dict["links"] = links
        if contact_methods is not UNSET:
            field_dict["contact_methods"] = contact_methods
        if addresses is not UNSET:
            field_dict["addresses"] = addresses
        if additional_identifiers is not UNSET:
            field_dict["additional_identifiers"] = additional_identifiers
        if events is not UNSET:
            field_dict["events"] = events

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.observation_additional_identifier import ObservationAdditionalIdentifier
        from ..models.observation_address import ObservationAddress
        from ..models.observation_contact_method import ObservationContactMethod
        from ..models.observation_event_item import ObservationEventItem
        from ..models.observation_link import ObservationLink
        from ..models.observation_name import ObservationName
        from ..models.observation_role_assignment import ObservationRoleAssignment

        d = dict(src_dict)
        identifier_type = d.pop("identifier_type")

        identifier_value = d.pop("identifier_value")

        _names = d.pop("names", UNSET)
        names: list[ObservationName] | Unset = UNSET
        if _names is not UNSET:
            names = []
            for names_item_data in _names:
                names_item = ObservationName.from_dict(names_item_data)

                names.append(names_item)

        def _parse_personal_pronouns(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        personal_pronouns = _parse_personal_pronouns(d.pop("personal_pronouns", UNSET))

        _role_assignments = d.pop("role_assignments", UNSET)
        role_assignments: list[ObservationRoleAssignment] | Unset = UNSET
        if _role_assignments is not UNSET:
            role_assignments = []
            for role_assignments_item_data in _role_assignments:
                role_assignments_item = ObservationRoleAssignment.from_dict(role_assignments_item_data)

                role_assignments.append(role_assignments_item)

        _links = d.pop("links", UNSET)
        links: list[ObservationLink] | Unset = UNSET
        if _links is not UNSET:
            links = []
            for links_item_data in _links:
                links_item = ObservationLink.from_dict(links_item_data)

                links.append(links_item)

        _contact_methods = d.pop("contact_methods", UNSET)
        contact_methods: list[ObservationContactMethod] | Unset = UNSET
        if _contact_methods is not UNSET:
            contact_methods = []
            for contact_methods_item_data in _contact_methods:
                contact_methods_item = ObservationContactMethod.from_dict(contact_methods_item_data)

                contact_methods.append(contact_methods_item)

        _addresses = d.pop("addresses", UNSET)
        addresses: list[ObservationAddress] | Unset = UNSET
        if _addresses is not UNSET:
            addresses = []
            for addresses_item_data in _addresses:
                addresses_item = ObservationAddress.from_dict(addresses_item_data)

                addresses.append(addresses_item)

        _additional_identifiers = d.pop("additional_identifiers", UNSET)
        additional_identifiers: list[ObservationAdditionalIdentifier] | Unset = UNSET
        if _additional_identifiers is not UNSET:
            additional_identifiers = []
            for additional_identifiers_item_data in _additional_identifiers:
                additional_identifiers_item = ObservationAdditionalIdentifier.from_dict(
                    additional_identifiers_item_data
                )

                additional_identifiers.append(additional_identifiers_item)

        _events = d.pop("events", UNSET)
        events: list[ObservationEventItem] | Unset = UNSET
        if _events is not UNSET:
            events = []
            for events_item_data in _events:
                events_item = ObservationEventItem.from_dict(events_item_data)

                events.append(events_item)

        people_observation_request = cls(
            identifier_type=identifier_type,
            identifier_value=identifier_value,
            names=names,
            personal_pronouns=personal_pronouns,
            role_assignments=role_assignments,
            links=links,
            contact_methods=contact_methods,
            addresses=addresses,
            additional_identifiers=additional_identifiers,
            events=events,
        )

        people_observation_request.additional_properties = d
        return people_observation_request

    @property
    def additional_keys(self) -> list[str]:
        return list(self.additional_properties.keys())

    def __getitem__(self, key: str) -> Any:
        return self.additional_properties[key]

    def __setitem__(self, key: str, value: Any) -> None:
        self.additional_properties[key] = value

    def __delitem__(self, key: str) -> None:
        del self.additional_properties[key]

    def __contains__(self, key: str) -> bool:
        return key in self.additional_properties
