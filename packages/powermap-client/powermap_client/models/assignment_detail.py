from __future__ import annotations

import datetime
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.assignment_address import AssignmentAddress
    from ..models.assignment_contact_method import AssignmentContactMethod
    from ..models.assignment_link import AssignmentLink


T = TypeVar("T", bound="AssignmentDetail")


@_attrs_define
class AssignmentDetail:
    """Full assignment record including links, contact methods, and addresses.

    Attributes:
        id (str):
        person_id (str):
        role_id (str):
        is_current (bool):
        created_at (None | str):
        updated_at (None | str):
        start_date (datetime.date | None | Unset):
        end_date (datetime.date | None | Unset):
        notes (None | str | Unset):
        archived_at (None | str | Unset):
        links (list[AssignmentLink] | Unset):
        contact_methods (list[AssignmentContactMethod] | Unset):
        addresses (list[AssignmentAddress] | Unset):
    """

    id: str
    person_id: str
    role_id: str
    is_current: bool
    created_at: None | str
    updated_at: None | str
    start_date: datetime.date | None | Unset = UNSET
    end_date: datetime.date | None | Unset = UNSET
    notes: None | str | Unset = UNSET
    archived_at: None | str | Unset = UNSET
    links: list[AssignmentLink] | Unset = UNSET
    contact_methods: list[AssignmentContactMethod] | Unset = UNSET
    addresses: list[AssignmentAddress] | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        id = self.id

        person_id = self.person_id

        role_id = self.role_id

        is_current = self.is_current

        created_at: None | str
        created_at = self.created_at

        updated_at: None | str
        updated_at = self.updated_at

        start_date: None | str | Unset
        if isinstance(self.start_date, Unset):
            start_date = UNSET
        elif isinstance(self.start_date, datetime.date):
            start_date = self.start_date.isoformat()
        else:
            start_date = self.start_date

        end_date: None | str | Unset
        if isinstance(self.end_date, Unset):
            end_date = UNSET
        elif isinstance(self.end_date, datetime.date):
            end_date = self.end_date.isoformat()
        else:
            end_date = self.end_date

        notes: None | str | Unset
        if isinstance(self.notes, Unset):
            notes = UNSET
        else:
            notes = self.notes

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
                "person_id": person_id,
                "role_id": role_id,
                "is_current": is_current,
                "created_at": created_at,
                "updated_at": updated_at,
            }
        )
        if start_date is not UNSET:
            field_dict["start_date"] = start_date
        if end_date is not UNSET:
            field_dict["end_date"] = end_date
        if notes is not UNSET:
            field_dict["notes"] = notes
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
        from ..models.assignment_address import AssignmentAddress
        from ..models.assignment_contact_method import AssignmentContactMethod
        from ..models.assignment_link import AssignmentLink

        d = dict(src_dict)
        id = d.pop("id")

        person_id = d.pop("person_id")

        role_id = d.pop("role_id")

        is_current = d.pop("is_current")

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

        def _parse_start_date(data: object) -> datetime.date | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, str):
                    raise TypeError()
                start_date_type_0 = datetime.date.fromisoformat(data)

                return start_date_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(datetime.date | None | Unset, data)

        start_date = _parse_start_date(d.pop("start_date", UNSET))

        def _parse_end_date(data: object) -> datetime.date | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, str):
                    raise TypeError()
                end_date_type_0 = datetime.date.fromisoformat(data)

                return end_date_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(datetime.date | None | Unset, data)

        end_date = _parse_end_date(d.pop("end_date", UNSET))

        def _parse_notes(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        notes = _parse_notes(d.pop("notes", UNSET))

        def _parse_archived_at(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        archived_at = _parse_archived_at(d.pop("archived_at", UNSET))

        _links = d.pop("links", UNSET)
        links: list[AssignmentLink] | Unset = UNSET
        if _links is not UNSET:
            links = []
            for links_item_data in _links:
                links_item = AssignmentLink.from_dict(links_item_data)

                links.append(links_item)

        _contact_methods = d.pop("contact_methods", UNSET)
        contact_methods: list[AssignmentContactMethod] | Unset = UNSET
        if _contact_methods is not UNSET:
            contact_methods = []
            for contact_methods_item_data in _contact_methods:
                contact_methods_item = AssignmentContactMethod.from_dict(contact_methods_item_data)

                contact_methods.append(contact_methods_item)

        _addresses = d.pop("addresses", UNSET)
        addresses: list[AssignmentAddress] | Unset = UNSET
        if _addresses is not UNSET:
            addresses = []
            for addresses_item_data in _addresses:
                addresses_item = AssignmentAddress.from_dict(addresses_item_data)

                addresses.append(addresses_item)

        assignment_detail = cls(
            id=id,
            person_id=person_id,
            role_id=role_id,
            is_current=is_current,
            created_at=created_at,
            updated_at=updated_at,
            start_date=start_date,
            end_date=end_date,
            notes=notes,
            archived_at=archived_at,
            links=links,
            contact_methods=contact_methods,
            addresses=addresses,
        )

        assignment_detail.additional_properties = d
        return assignment_detail

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
