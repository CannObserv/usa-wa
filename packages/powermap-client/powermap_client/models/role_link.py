from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar

from attrs import define as _attrs_define
from attrs import field as _attrs_field

T = TypeVar("T", bound="RoleLink")


@_attrs_define
class RoleLink:
    """A link attached to a role.

    Attributes:
        id (str):
        url (str):
        link_type_id (str):
        link_type_slug (str):
        link_type_name (str):
        is_active (bool):
    """

    id: str
    url: str
    link_type_id: str
    link_type_slug: str
    link_type_name: str
    is_active: bool
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        id = self.id

        url = self.url

        link_type_id = self.link_type_id

        link_type_slug = self.link_type_slug

        link_type_name = self.link_type_name

        is_active = self.is_active

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "id": id,
                "url": url,
                "link_type_id": link_type_id,
                "link_type_slug": link_type_slug,
                "link_type_name": link_type_name,
                "is_active": is_active,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        id = d.pop("id")

        url = d.pop("url")

        link_type_id = d.pop("link_type_id")

        link_type_slug = d.pop("link_type_slug")

        link_type_name = d.pop("link_type_name")

        is_active = d.pop("is_active")

        role_link = cls(
            id=id,
            url=url,
            link_type_id=link_type_id,
            link_type_slug=link_type_slug,
            link_type_name=link_type_name,
            is_active=is_active,
        )

        role_link.additional_properties = d
        return role_link

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
