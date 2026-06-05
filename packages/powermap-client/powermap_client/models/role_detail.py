from __future__ import annotations

import datetime
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.role_address import RoleAddress
    from ..models.role_contact_method import RoleContactMethod
    from ..models.role_link import RoleLink


T = TypeVar("T", bound="RoleDetail")


@_attrs_define
class RoleDetail:
    """Full role record including links, contact methods, and addresses.

    Attributes:
        id (str):
        organization_id (str):
        title (str):
        created_at (None | str):
        updated_at (None | str):
        notes (None | str | Unset):
        established_on (datetime.date | None | Unset):
        abolished_on (datetime.date | None | Unset):
        archived_at (None | str | Unset):
        links (list[RoleLink] | Unset):
        contact_methods (list[RoleContactMethod] | Unset):
        addresses (list[RoleAddress] | Unset):
    """

    id: str
    organization_id: str
    title: str
    created_at: None | str
    updated_at: None | str
    notes: None | str | Unset = UNSET
    established_on: datetime.date | None | Unset = UNSET
    abolished_on: datetime.date | None | Unset = UNSET
    archived_at: None | str | Unset = UNSET
    links: list[RoleLink] | Unset = UNSET
    contact_methods: list[RoleContactMethod] | Unset = UNSET
    addresses: list[RoleAddress] | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        id = self.id

        organization_id = self.organization_id

        title = self.title

        created_at: None | str
        created_at = self.created_at

        updated_at: None | str
        updated_at = self.updated_at

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

        archived_at: None | str | Unset
        if isinstance(self.archived_at, Unset):
            archived_at = UNSET
        else:
            archived_at = self.archived_at

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
                "id": id,
                "organization_id": organization_id,
                "title": title,
                "created_at": created_at,
                "updated_at": updated_at,
            }
        )
        if notes is not UNSET:
            field_dict["notes"] = notes
        if established_on is not UNSET:
            field_dict["established_on"] = established_on
        if abolished_on is not UNSET:
            field_dict["abolished_on"] = abolished_on
        if archived_at is not UNSET:
            field_dict["archived_at"] = archived_at
        if links is not UNSET:
            field_dict["links"] = links
        if contact_methods is not UNSET:
            field_dict["contact_methods"] = contact_methods
        if addresses is not UNSET:
            field_dict["addresses"] = addresses

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.role_address import RoleAddress
        from ..models.role_contact_method import RoleContactMethod
        from ..models.role_link import RoleLink

        d = dict(src_dict)
        id = d.pop("id")

        organization_id = d.pop("organization_id")

        title = d.pop("title")

        def _parse_created_at(data: object) -> None | str:
            if data is None:
                return data
            return cast(None | str, data)

        created_at = _parse_created_at(d.pop("created_at"))

        def _parse_updated_at(data: object) -> None | str:
            if data is None:
                return data
            return cast(None | str, data)

        updated_at = _parse_updated_at(d.pop("updated_at"))

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

        def _parse_archived_at(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        archived_at = _parse_archived_at(d.pop("archived_at", UNSET))

        _links = d.pop("links", UNSET)
        links: list[RoleLink] | Unset = UNSET
        if _links is not UNSET:
            links = []
            for links_item_data in _links:
                links_item = RoleLink.from_dict(links_item_data)

                links.append(links_item)

        _contact_methods = d.pop("contact_methods", UNSET)
        contact_methods: list[RoleContactMethod] | Unset = UNSET
        if _contact_methods is not UNSET:
            contact_methods = []
            for contact_methods_item_data in _contact_methods:
                contact_methods_item = RoleContactMethod.from_dict(contact_methods_item_data)

                contact_methods.append(contact_methods_item)

        _addresses = d.pop("addresses", UNSET)
        addresses: list[RoleAddress] | Unset = UNSET
        if _addresses is not UNSET:
            addresses = []
            for addresses_item_data in _addresses:
                addresses_item = RoleAddress.from_dict(addresses_item_data)

                addresses.append(addresses_item)

        role_detail = cls(
            id=id,
            organization_id=organization_id,
            title=title,
            created_at=created_at,
            updated_at=updated_at,
            notes=notes,
            established_on=established_on,
            abolished_on=abolished_on,
            archived_at=archived_at,
            links=links,
            contact_methods=contact_methods,
            addresses=addresses,
        )

        role_detail.additional_properties = d
        return role_detail

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
