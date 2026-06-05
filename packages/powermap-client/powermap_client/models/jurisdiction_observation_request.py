from __future__ import annotations

import datetime
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.observation_additional_identifier import ObservationAdditionalIdentifier
    from ..models.observation_address import ObservationAddress
    from ..models.observation_contact_method import ObservationContactMethod
    from ..models.observation_link import ObservationLink


T = TypeVar("T", bound="JurisdictionObservationRequest")


@_attrs_define
class JurisdictionObservationRequest:
    """Payload for POST /api/v1/jurisdictions/observations.

    Attributes:
        identifier_type (str):
        identifier_value (str):
        jurisdiction_slug (None | str | Unset):
        jurisdiction_name (None | str | Unset):
        jurisdiction_type_slug (None | str | Unset):
        jurisdiction_valid_from (datetime.date | None | Unset):
        jurisdiction_valid_until (datetime.date | None | Unset):
        jurisdiction_notes (None | str | Unset):
        links (list[ObservationLink] | Unset):
        contact_methods (list[ObservationContactMethod] | Unset):
        addresses (list[ObservationAddress] | Unset):
        additional_identifiers (list[ObservationAdditionalIdentifier] | Unset):
    """

    identifier_type: str
    identifier_value: str
    jurisdiction_slug: None | str | Unset = UNSET
    jurisdiction_name: None | str | Unset = UNSET
    jurisdiction_type_slug: None | str | Unset = UNSET
    jurisdiction_valid_from: datetime.date | None | Unset = UNSET
    jurisdiction_valid_until: datetime.date | None | Unset = UNSET
    jurisdiction_notes: None | str | Unset = UNSET
    links: list[ObservationLink] | Unset = UNSET
    contact_methods: list[ObservationContactMethod] | Unset = UNSET
    addresses: list[ObservationAddress] | Unset = UNSET
    additional_identifiers: list[ObservationAdditionalIdentifier] | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        identifier_type = self.identifier_type

        identifier_value = self.identifier_value

        jurisdiction_slug: None | str | Unset
        if isinstance(self.jurisdiction_slug, Unset):
            jurisdiction_slug = UNSET
        else:
            jurisdiction_slug = self.jurisdiction_slug

        jurisdiction_name: None | str | Unset
        if isinstance(self.jurisdiction_name, Unset):
            jurisdiction_name = UNSET
        else:
            jurisdiction_name = self.jurisdiction_name

        jurisdiction_type_slug: None | str | Unset
        if isinstance(self.jurisdiction_type_slug, Unset):
            jurisdiction_type_slug = UNSET
        else:
            jurisdiction_type_slug = self.jurisdiction_type_slug

        jurisdiction_valid_from: None | str | Unset
        if isinstance(self.jurisdiction_valid_from, Unset):
            jurisdiction_valid_from = UNSET
        elif isinstance(self.jurisdiction_valid_from, datetime.date):
            jurisdiction_valid_from = self.jurisdiction_valid_from.isoformat()
        else:
            jurisdiction_valid_from = self.jurisdiction_valid_from

        jurisdiction_valid_until: None | str | Unset
        if isinstance(self.jurisdiction_valid_until, Unset):
            jurisdiction_valid_until = UNSET
        elif isinstance(self.jurisdiction_valid_until, datetime.date):
            jurisdiction_valid_until = self.jurisdiction_valid_until.isoformat()
        else:
            jurisdiction_valid_until = self.jurisdiction_valid_until

        jurisdiction_notes: None | str | Unset
        if isinstance(self.jurisdiction_notes, Unset):
            jurisdiction_notes = UNSET
        else:
            jurisdiction_notes = self.jurisdiction_notes

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

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "identifier_type": identifier_type,
                "identifier_value": identifier_value,
            }
        )
        if jurisdiction_slug is not UNSET:
            field_dict["jurisdiction_slug"] = jurisdiction_slug
        if jurisdiction_name is not UNSET:
            field_dict["jurisdiction_name"] = jurisdiction_name
        if jurisdiction_type_slug is not UNSET:
            field_dict["jurisdiction_type_slug"] = jurisdiction_type_slug
        if jurisdiction_valid_from is not UNSET:
            field_dict["jurisdiction_valid_from"] = jurisdiction_valid_from
        if jurisdiction_valid_until is not UNSET:
            field_dict["jurisdiction_valid_until"] = jurisdiction_valid_until
        if jurisdiction_notes is not UNSET:
            field_dict["jurisdiction_notes"] = jurisdiction_notes
        if links is not UNSET:
            field_dict["links"] = links
        if contact_methods is not UNSET:
            field_dict["contact_methods"] = contact_methods
        if addresses is not UNSET:
            field_dict["addresses"] = addresses
        if additional_identifiers is not UNSET:
            field_dict["additional_identifiers"] = additional_identifiers

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.observation_additional_identifier import ObservationAdditionalIdentifier
        from ..models.observation_address import ObservationAddress
        from ..models.observation_contact_method import ObservationContactMethod
        from ..models.observation_link import ObservationLink

        d = dict(src_dict)
        identifier_type = d.pop("identifier_type")

        identifier_value = d.pop("identifier_value")

        def _parse_jurisdiction_slug(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        jurisdiction_slug = _parse_jurisdiction_slug(d.pop("jurisdiction_slug", UNSET))

        def _parse_jurisdiction_name(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        jurisdiction_name = _parse_jurisdiction_name(d.pop("jurisdiction_name", UNSET))

        def _parse_jurisdiction_type_slug(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        jurisdiction_type_slug = _parse_jurisdiction_type_slug(d.pop("jurisdiction_type_slug", UNSET))

        def _parse_jurisdiction_valid_from(data: object) -> datetime.date | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, str):
                    raise TypeError()
                jurisdiction_valid_from_type_0 = datetime.date.fromisoformat(data)

                return jurisdiction_valid_from_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(datetime.date | None | Unset, data)

        jurisdiction_valid_from = _parse_jurisdiction_valid_from(d.pop("jurisdiction_valid_from", UNSET))

        def _parse_jurisdiction_valid_until(data: object) -> datetime.date | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, str):
                    raise TypeError()
                jurisdiction_valid_until_type_0 = datetime.date.fromisoformat(data)

                return jurisdiction_valid_until_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(datetime.date | None | Unset, data)

        jurisdiction_valid_until = _parse_jurisdiction_valid_until(d.pop("jurisdiction_valid_until", UNSET))

        def _parse_jurisdiction_notes(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        jurisdiction_notes = _parse_jurisdiction_notes(d.pop("jurisdiction_notes", UNSET))

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

        jurisdiction_observation_request = cls(
            identifier_type=identifier_type,
            identifier_value=identifier_value,
            jurisdiction_slug=jurisdiction_slug,
            jurisdiction_name=jurisdiction_name,
            jurisdiction_type_slug=jurisdiction_type_slug,
            jurisdiction_valid_from=jurisdiction_valid_from,
            jurisdiction_valid_until=jurisdiction_valid_until,
            jurisdiction_notes=jurisdiction_notes,
            links=links,
            contact_methods=contact_methods,
            addresses=addresses,
            additional_identifiers=additional_identifiers,
        )

        jurisdiction_observation_request.additional_properties = d
        return jurisdiction_observation_request

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
