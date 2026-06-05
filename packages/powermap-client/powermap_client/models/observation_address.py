from __future__ import annotations

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
    """

    raw_input: str
    address_type: ObservationAddressAddressType | Unset = ObservationAddressAddressType.OTHER
    display_name: None | str | Unset = UNSET
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

        observation_address = cls(
            raw_input=raw_input,
            address_type=address_type,
            display_name=display_name,
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
