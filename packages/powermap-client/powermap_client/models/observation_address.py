from __future__ import annotations

import datetime
from collections.abc import Mapping
from typing import Any, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..models.observation_address_address_type import ObservationAddressAddressType
from ..types import UNSET, Unset

T = TypeVar("T", bound="ObservationAddress")


@_attrs_define
class ObservationAddress:
    """An address claim included in an observation.

    Attributes:
        raw_input (str):
        address_type (ObservationAddressAddressType | Unset):  Default: ObservationAddressAddressType.OTHER.
        display_name (None | str | Unset):
        valid_from (datetime.date | None | Unset):
        valid_until (datetime.date | None | Unset):
    """

    raw_input: str
    address_type: ObservationAddressAddressType | Unset = ObservationAddressAddressType.OTHER
    display_name: None | str | Unset = UNSET
    valid_from: datetime.date | None | Unset = UNSET
    valid_until: datetime.date | None | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        raw_input = self.raw_input

        address_type: str | Unset = UNSET
        if not isinstance(self.address_type, Unset):
            address_type = self.address_type.value

        display_name: None | str | Unset
        if isinstance(self.display_name, Unset):
            display_name = UNSET
        else:
            display_name = self.display_name

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
                "raw_input": raw_input,
            }
        )
        if address_type is not UNSET:
            field_dict["address_type"] = address_type
        if display_name is not UNSET:
            field_dict["display_name"] = display_name
        if valid_from is not UNSET:
            field_dict["valid_from"] = valid_from
        if valid_until is not UNSET:
            field_dict["valid_until"] = valid_until

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        raw_input = d.pop("raw_input")

        _address_type = d.pop("address_type", UNSET)
        address_type: ObservationAddressAddressType | Unset
        if isinstance(_address_type, Unset):
            address_type = UNSET
        else:
            address_type = ObservationAddressAddressType(_address_type)

        def _parse_display_name(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        display_name = _parse_display_name(d.pop("display_name", UNSET))

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

        observation_address = cls(
            raw_input=raw_input,
            address_type=address_type,
            display_name=display_name,
            valid_from=valid_from,
            valid_until=valid_until,
        )

        observation_address.additional_properties = d
        return observation_address

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
