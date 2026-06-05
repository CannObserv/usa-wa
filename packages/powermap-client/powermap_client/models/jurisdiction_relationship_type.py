from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar

from attrs import define as _attrs_define
from attrs import field as _attrs_field

T = TypeVar("T", bound="JurisdictionRelationshipType")


@_attrs_define
class JurisdictionRelationshipType:
    """A jurisdiction relationship type from the lookup table.

    Attributes:
        id (str):
        slug (str):
        display_name (str):
        category (str):
        is_symmetric (bool):
    """

    id: str
    slug: str
    display_name: str
    category: str
    is_symmetric: bool
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        id = self.id

        slug = self.slug

        display_name = self.display_name

        category = self.category

        is_symmetric = self.is_symmetric

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "id": id,
                "slug": slug,
                "display_name": display_name,
                "category": category,
                "is_symmetric": is_symmetric,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        id = d.pop("id")

        slug = d.pop("slug")

        display_name = d.pop("display_name")

        category = d.pop("category")

        is_symmetric = d.pop("is_symmetric")

        jurisdiction_relationship_type = cls(
            id=id,
            slug=slug,
            display_name=display_name,
            category=category,
            is_symmetric=is_symmetric,
        )

        jurisdiction_relationship_type.additional_properties = d
        return jurisdiction_relationship_type

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
