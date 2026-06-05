from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar

from attrs import define as _attrs_define
from attrs import field as _attrs_field

T = TypeVar("T", bound="BodyIdentifierTypeCreateAdminSettingsIdentifierTypesPost")


@_attrs_define
class BodyIdentifierTypeCreateAdminSettingsIdentifierTypesPost:
    """
    Attributes:
        display_name (str):
        slug (str):
        full_name (str):
        entity_type (str):
    """

    display_name: str
    slug: str
    full_name: str
    entity_type: str
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        display_name = self.display_name

        slug = self.slug

        full_name = self.full_name

        entity_type = self.entity_type

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "display_name": display_name,
                "slug": slug,
                "full_name": full_name,
                "entity_type": entity_type,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        display_name = d.pop("display_name")

        slug = d.pop("slug")

        full_name = d.pop("full_name")

        entity_type = d.pop("entity_type")

        body_identifier_type_create_admin_settings_identifier_types_post = cls(
            display_name=display_name,
            slug=slug,
            full_name=full_name,
            entity_type=entity_type,
        )

        body_identifier_type_create_admin_settings_identifier_types_post.additional_properties = d
        return body_identifier_type_create_admin_settings_identifier_types_post

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
