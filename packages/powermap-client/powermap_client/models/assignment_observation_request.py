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


T = TypeVar("T", bound="AssignmentObservationRequest")


@_attrs_define
class AssignmentObservationRequest:
    """Payload for POST /api/v1/assignments/observations.

    Attributes:
        person_id (str):
        role_id (str):
        start_date (datetime.date | None | Unset):
        end_date (datetime.date | None | Unset):
        is_current (bool | Unset):  Default: False.
        notes (None | str | Unset):
        links (list[ObservationLink] | Unset):
        contact_methods (list[ObservationContactMethod] | Unset):
        addresses (list[ObservationAddress] | Unset):
    """

    person_id: str
    role_id: str
    start_date: datetime.date | None | Unset = UNSET
    end_date: datetime.date | None | Unset = UNSET
    is_current: bool | Unset = False
    notes: None | str | Unset = UNSET
    links: list[ObservationLink] | Unset = UNSET
    contact_methods: list[ObservationContactMethod] | Unset = UNSET
    addresses: list[ObservationAddress] | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        person_id = self.person_id

        role_id = self.role_id

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

        is_current = self.is_current

        notes: None | str | Unset
        if isinstance(self.notes, Unset):
            notes = UNSET
        else:
            notes = self.notes

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
                "person_id": person_id,
                "role_id": role_id,
            }
        )
        if start_date is not UNSET:
            field_dict["start_date"] = start_date
        if end_date is not UNSET:
            field_dict["end_date"] = end_date
        if is_current is not UNSET:
            field_dict["is_current"] = is_current
        if notes is not UNSET:
            field_dict["notes"] = notes
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
        person_id = d.pop("person_id")

        role_id = d.pop("role_id")

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

        is_current = d.pop("is_current", UNSET)

        def _parse_notes(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        notes = _parse_notes(d.pop("notes", UNSET))

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

        assignment_observation_request = cls(
            person_id=person_id,
            role_id=role_id,
            start_date=start_date,
            end_date=end_date,
            is_current=is_current,
            notes=notes,
            links=links,
            contact_methods=contact_methods,
            addresses=addresses,
        )

        assignment_observation_request.additional_properties = d
        return assignment_observation_request

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
