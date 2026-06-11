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
    from ..models.observation_jurisdiction_affiliation import ObservationJurisdictionAffiliation
    from ..models.observation_link import ObservationLink
    from ..models.observation_name import ObservationName


T = TypeVar("T", bound="OrganizationObservationRequest")


@_attrs_define
class OrganizationObservationRequest:
    """Payload for POST /api/v1/orgs/observations.

    Attributes:
        identifier_type (str):
        identifier_value (str):
        names (list[ObservationName] | Unset):
        org_acronyms (list[str] | Unset):
        organization_parent_id (None | str | Unset):
        organization_parent_name (None | str | Unset):
        organization_parent_acronym (None | str | Unset):
        links (list[ObservationLink] | Unset):
        contact_methods (list[ObservationContactMethod] | Unset):
        addresses (list[ObservationAddress] | Unset):
        additional_identifiers (list[ObservationAdditionalIdentifier] | Unset):
        jurisdiction_affiliations (list[ObservationJurisdictionAffiliation] | Unset):
        events (list[ObservationEventItem] | Unset):
    """

    identifier_type: str
    identifier_value: str
    names: list[ObservationName] | Unset = UNSET
    org_acronyms: list[str] | Unset = UNSET
    organization_parent_id: None | str | Unset = UNSET
    organization_parent_name: None | str | Unset = UNSET
    organization_parent_acronym: None | str | Unset = UNSET
    links: list[ObservationLink] | Unset = UNSET
    contact_methods: list[ObservationContactMethod] | Unset = UNSET
    addresses: list[ObservationAddress] | Unset = UNSET
    additional_identifiers: list[ObservationAdditionalIdentifier] | Unset = UNSET
    jurisdiction_affiliations: list[ObservationJurisdictionAffiliation] | Unset = UNSET
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

        org_acronyms: list[str] | Unset = UNSET
        if not isinstance(self.org_acronyms, Unset):
            org_acronyms = self.org_acronyms

        organization_parent_id: None | str | Unset
        if isinstance(self.organization_parent_id, Unset):
            organization_parent_id = UNSET
        else:
            organization_parent_id = self.organization_parent_id

        organization_parent_name: None | str | Unset
        if isinstance(self.organization_parent_name, Unset):
            organization_parent_name = UNSET
        else:
            organization_parent_name = self.organization_parent_name

        organization_parent_acronym: None | str | Unset
        if isinstance(self.organization_parent_acronym, Unset):
            organization_parent_acronym = UNSET
        else:
            organization_parent_acronym = self.organization_parent_acronym

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

        jurisdiction_affiliations: list[dict[str, Any]] | Unset = UNSET
        if not isinstance(self.jurisdiction_affiliations, Unset):
            jurisdiction_affiliations = []
            for jurisdiction_affiliations_item_data in self.jurisdiction_affiliations:
                jurisdiction_affiliations_item = jurisdiction_affiliations_item_data.to_dict()
                jurisdiction_affiliations.append(jurisdiction_affiliations_item)

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
        if org_acronyms is not UNSET:
            field_dict["org_acronyms"] = org_acronyms
        if organization_parent_id is not UNSET:
            field_dict["organization_parent_id"] = organization_parent_id
        if organization_parent_name is not UNSET:
            field_dict["organization_parent_name"] = organization_parent_name
        if organization_parent_acronym is not UNSET:
            field_dict["organization_parent_acronym"] = organization_parent_acronym
        if links is not UNSET:
            field_dict["links"] = links
        if contact_methods is not UNSET:
            field_dict["contact_methods"] = contact_methods
        if addresses is not UNSET:
            field_dict["addresses"] = addresses
        if additional_identifiers is not UNSET:
            field_dict["additional_identifiers"] = additional_identifiers
        if jurisdiction_affiliations is not UNSET:
            field_dict["jurisdiction_affiliations"] = jurisdiction_affiliations
        if events is not UNSET:
            field_dict["events"] = events

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.observation_additional_identifier import ObservationAdditionalIdentifier
        from ..models.observation_address import ObservationAddress
        from ..models.observation_contact_method import ObservationContactMethod
        from ..models.observation_event_item import ObservationEventItem
        from ..models.observation_jurisdiction_affiliation import ObservationJurisdictionAffiliation
        from ..models.observation_link import ObservationLink
        from ..models.observation_name import ObservationName

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

        org_acronyms = cast(list[str], d.pop("org_acronyms", UNSET))

        def _parse_organization_parent_id(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        organization_parent_id = _parse_organization_parent_id(d.pop("organization_parent_id", UNSET))

        def _parse_organization_parent_name(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        organization_parent_name = _parse_organization_parent_name(d.pop("organization_parent_name", UNSET))

        def _parse_organization_parent_acronym(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        organization_parent_acronym = _parse_organization_parent_acronym(d.pop("organization_parent_acronym", UNSET))

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

        _jurisdiction_affiliations = d.pop("jurisdiction_affiliations", UNSET)
        jurisdiction_affiliations: list[ObservationJurisdictionAffiliation] | Unset = UNSET
        if _jurisdiction_affiliations is not UNSET:
            jurisdiction_affiliations = []
            for jurisdiction_affiliations_item_data in _jurisdiction_affiliations:
                jurisdiction_affiliations_item = ObservationJurisdictionAffiliation.from_dict(
                    jurisdiction_affiliations_item_data
                )

                jurisdiction_affiliations.append(jurisdiction_affiliations_item)

        _events = d.pop("events", UNSET)
        events: list[ObservationEventItem] | Unset = UNSET
        if _events is not UNSET:
            events = []
            for events_item_data in _events:
                events_item = ObservationEventItem.from_dict(events_item_data)

                events.append(events_item)

        organization_observation_request = cls(
            identifier_type=identifier_type,
            identifier_value=identifier_value,
            names=names,
            org_acronyms=org_acronyms,
            organization_parent_id=organization_parent_id,
            organization_parent_name=organization_parent_name,
            organization_parent_acronym=organization_parent_acronym,
            links=links,
            contact_methods=contact_methods,
            addresses=addresses,
            additional_identifiers=additional_identifiers,
            jurisdiction_affiliations=jurisdiction_affiliations,
            events=events,
        )

        organization_observation_request.additional_properties = d
        return organization_observation_request

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
