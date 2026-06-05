from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..types import UNSET, Unset

T = TypeVar("T", bound="BodyLinkEditRowPostAdminOrgsEntityIdLinksLinkIdEditRowPost")


@_attrs_define
class BodyLinkEditRowPostAdminOrgsEntityIdLinksLinkIdEditRowPost:
    """
    Attributes:
        url (str):
        link_type_id (str):
        is_active (str | Unset):  Default: ''.
    """

    url: str
    link_type_id: str
    is_active: str | Unset = ""
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        url = self.url

        link_type_id = self.link_type_id

        is_active = self.is_active

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "url": url,
                "link_type_id": link_type_id,
            }
        )
        if is_active is not UNSET:
            field_dict["is_active"] = is_active

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        url = d.pop("url")

        link_type_id = d.pop("link_type_id")

        is_active = d.pop("is_active", UNSET)

        body_link_edit_row_post_admin_orgs_entity_id_links_link_id_edit_row_post = cls(
            url=url,
            link_type_id=link_type_id,
            is_active=is_active,
        )

        body_link_edit_row_post_admin_orgs_entity_id_links_link_id_edit_row_post.additional_properties = d
        return body_link_edit_row_post_admin_orgs_entity_id_links_link_id_edit_row_post

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
