from __future__ import annotations

import datetime
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.observation_address import ObservationAddress
    from ..models.observation_contact_method import ObservationContactMethod
    from ..models.observation_link import ObservationLink


T = TypeVar("T", bound="RoleObservationRequest")


@_attrs_define
class RoleObservationRequest:
    """Payload for POST /api/v1/roles/observations.

    Attributes:
        organization_id (str):
        title (str):
        notes (None | str | Unset):
        established_on (datetime.date | None | Unset):
        abolished_on (datetime.date | None | Unset):
        links (list[ObservationLink] | Unset):
        contact_methods (list[ObservationContactMethod] | Unset):
        addresses (list[ObservationAddress] | Unset):
    """

    organization_id: str
    title: str
    notes: None | str | Unset = UNSET
    established_on: datetime.date | None | Unset = UNSET
    abolished_on: datetime.date | None | Unset = UNSET
    links: list[ObservationLink] | Unset = UNSET
    contact_methods: list[ObservationContactMethod] | Unset = UNSET
    addresses: list[ObservationAddress] | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        organization_id = self.organization_id

        title = self.title

        notes: None | str | Unset
        if isinstance(self.notes, Unset):
            notes = UNSET
        else:
            notes = self.notes

        established_on: None | str | Unset
        if isinstance(self.established_on, Unset):
            established_on = UNSET
        elif isinstance(self.established_on, datetime.date):
            established_on = self.established_on.isoformat()
        else:
            established_on = self.established_on

        abolished_on: None | str | Unset
        if isinstance(self.abolished_on, Unset):
            abolished_on = UNSET
        elif isinstance(self.abolished_on, datetime.date):
            abolished_on = self.abolished_on.isoformat()
        else:
            abolished_on = self.abolished_on

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

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "organization_id": organization_id,
                "title": title,
            }
        )
        if notes is not UNSET:
            field_dict["notes"] = notes
        if established_on is not UNSET:
            field_dict["established_on"] = established_on
        if abolished_on is not UNSET:
            field_dict["abolished_on"] = abolished_on
        if links is not UNSET:
            field_dict["links"] = links
        if contact_methods is not UNSET:
            field_dict["contact_methods"] = contact_methods
        if addresses is not UNSET:
            field_dict["addresses"] = addresses

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.observation_address import ObservationAddress
        from ..models.observation_contact_method import ObservationContactMethod
        from ..models.observation_link import ObservationLink

        d = dict(src_dict)
        organization_id = d.pop("organization_id")

        title = d.pop("title")

        def _parse_notes(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        notes = _parse_notes(d.pop("notes", UNSET))

        def _parse_established_on(data: object) -> datetime.date | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, str):
                    raise TypeError()
                established_on_type_0 = datetime.date.fromisoformat(data)

                return established_on_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(datetime.date | None | Unset, data)

        established_on = _parse_established_on(d.pop("established_on", UNSET))

        def _parse_abolished_on(data: object) -> datetime.date | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, str):
                    raise TypeError()
                abolished_on_type_0 = datetime.date.fromisoformat(data)

                return abolished_on_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(datetime.date | None | Unset, data)

        abolished_on = _parse_abolished_on(d.pop("abolished_on", UNSET))

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

        role_observation_request = cls(
            organization_id=organization_id,
            title=title,
            notes=notes,
            established_on=established_on,
            abolished_on=abolished_on,
            links=links,
            contact_methods=contact_methods,
            addresses=addresses,
        )

        role_observation_request.additional_properties = d
        return role_observation_request

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
