from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar

from attrs import define as _attrs_define
from attrs import field as _attrs_field

T = TypeVar("T", bound="OrgIdentifier")


@_attrs_define
class OrgIdentifier:
    """An external identifier attached to an organization.

    Attributes:
        id (str):
        type_id (str):
        type_slug (str):
        value (str):
    """

    id: str
    type_id: str
    type_slug: str
    value: str
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        id = self.id

        type_id = self.type_id

        type_slug = self.type_slug

        value = self.value

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "id": id,
                "type_id": type_id,
                "type_slug": type_slug,
                "value": value,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        id = d.pop("id")

        type_id = d.pop("type_id")

        type_slug = d.pop("type_slug")

        value = d.pop("value")

        org_identifier = cls(
            id=id,
            type_id=type_id,
            type_slug=type_slug,
            value=value,
        )

        org_identifier.additional_properties = d
        return org_identifier

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
