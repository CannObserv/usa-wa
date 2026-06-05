from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar

from attrs import define as _attrs_define
from attrs import field as _attrs_field

T = TypeVar("T", bound="OrgName")


@_attrs_define
class OrgName:
    """A single name variant for an organization.

    Attributes:
        id (str):
        name (str):
        name_type (str):
        is_canonical (bool):
    """

    id: str
    name: str
    name_type: str
    is_canonical: bool
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        id = self.id

        name = self.name

        name_type = self.name_type

        is_canonical = self.is_canonical

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "id": id,
                "name": name,
                "name_type": name_type,
                "is_canonical": is_canonical,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        id = d.pop("id")

        name = d.pop("name")

        name_type = d.pop("name_type")

        is_canonical = d.pop("is_canonical")

        org_name = cls(
            id=id,
            name=name,
            name_type=name_type,
            is_canonical=is_canonical,
        )

        org_name.additional_properties = d
        return org_name

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
