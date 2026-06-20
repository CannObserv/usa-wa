from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..models.observation_org_name_name_type import ObservationOrgNameNameType
from ..types import UNSET, Unset

T = TypeVar("T", bound="ObservationOrgName")


@_attrs_define
class ObservationOrgName:
    """A name claim included in an org observation.

    Attributes:
        name (str):
        name_type (ObservationOrgNameNameType | Unset):  Default: ObservationOrgNameNameType.LEGAL.
        is_canonical (bool | Unset):  Default: False.
    """

    name: str
    name_type: ObservationOrgNameNameType | Unset = ObservationOrgNameNameType.LEGAL
    is_canonical: bool | Unset = False
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        name = self.name

        name_type: str | Unset = UNSET
        if not isinstance(self.name_type, Unset):
            name_type = self.name_type.value

        is_canonical = self.is_canonical

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "name": name,
            }
        )
        if name_type is not UNSET:
            field_dict["name_type"] = name_type
        if is_canonical is not UNSET:
            field_dict["is_canonical"] = is_canonical

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        name = d.pop("name")

        _name_type = d.pop("name_type", UNSET)
        name_type: ObservationOrgNameNameType | Unset
        if isinstance(_name_type, Unset):
            name_type = UNSET
        else:
            name_type = ObservationOrgNameNameType(_name_type)

        is_canonical = d.pop("is_canonical", UNSET)

        observation_org_name = cls(
            name=name,
            name_type=name_type,
            is_canonical=is_canonical,
        )

        observation_org_name.additional_properties = d
        return observation_org_name

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
