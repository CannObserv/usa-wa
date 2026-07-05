from __future__ import annotations

import datetime
from collections.abc import Mapping
from typing import Any, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..types import UNSET, Unset

T = TypeVar("T", bound="AssignmentAddress")


@_attrs_define
class AssignmentAddress:
    """An address attached to a role assignment.

    Attributes:
        id (str):
        address_id (str):
        address_type (str):
        raw_input (None | str | Unset):
        standardized (None | str | Unset):
        valid_from (datetime.date | None | Unset):
        valid_until (datetime.date | None | Unset):
    """

    id: str
    address_id: str
    address_type: str
    raw_input: None | str | Unset = UNSET
    standardized: None | str | Unset = UNSET
    valid_from: datetime.date | None | Unset = UNSET
    valid_until: datetime.date | None | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        id = self.id

        address_id = self.address_id

        address_type = self.address_type

        raw_input: None | str | Unset
        if isinstance(self.raw_input, Unset):
            raw_input = UNSET
        else:
            raw_input = self.raw_input

        standardized: None | str | Unset
        if isinstance(self.standardized, Unset):
            standardized = UNSET
        else:
            standardized = self.standardized

        valid_from: None | str | Unset
        if isinstance(self.valid_from, Unset):
            valid_from = UNSET
        elif isinstance(self.valid_from, datetime.date):
            valid_from = self.valid_from.isoformat()
        else:
            valid_from = self.valid_from

        valid_until: None | str | Unset
        if isinstance(self.valid_until, Unset):
            valid_until = UNSET
        elif isinstance(self.valid_until, datetime.date):
            valid_until = self.valid_until.isoformat()
        else:
            valid_until = self.valid_until

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "id": id,
                "address_id": address_id,
                "address_type": address_type,
            }
        )
        if raw_input is not UNSET:
            field_dict["raw_input"] = raw_input
        if standardized is not UNSET:
            field_dict["standardized"] = standardized
        if valid_from is not UNSET:
            field_dict["valid_from"] = valid_from
        if valid_until is not UNSET:
            field_dict["valid_until"] = valid_until

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        id = d.pop("id")

        address_id = d.pop("address_id")

        address_type = d.pop("address_type")

        def _parse_raw_input(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        raw_input = _parse_raw_input(d.pop("raw_input", UNSET))

        def _parse_standardized(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        standardized = _parse_standardized(d.pop("standardized", UNSET))

        def _parse_valid_from(data: object) -> datetime.date | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, str):
                    raise TypeError()
                valid_from_type_0 = datetime.date.fromisoformat(data)

                return valid_from_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(datetime.date | None | Unset, data)

        valid_from = _parse_valid_from(d.pop("valid_from", UNSET))

        def _parse_valid_until(data: object) -> datetime.date | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, str):
                    raise TypeError()
                valid_until_type_0 = datetime.date.fromisoformat(data)

                return valid_until_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(datetime.date | None | Unset, data)

        valid_until = _parse_valid_until(d.pop("valid_until", UNSET))

        assignment_address = cls(
            id=id,
            address_id=address_id,
            address_type=address_type,
            raw_input=raw_input,
            standardized=standardized,
            valid_from=valid_from,
            valid_until=valid_until,
        )

        assignment_address.additional_properties = d
        return assignment_address

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
