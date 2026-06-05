from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar

from attrs import define as _attrs_define
from attrs import field as _attrs_field

T = TypeVar("T", bound="BodyChildrenAddAdminOrgsOrgIdChildrenPost")


@_attrs_define
class BodyChildrenAddAdminOrgsOrgIdChildrenPost:
    """
    Attributes:
        child_id (str):
    """

    child_id: str
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        child_id = self.child_id

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "child_id": child_id,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        child_id = d.pop("child_id")

        body_children_add_admin_orgs_org_id_children_post = cls(
            child_id=child_id,
        )

        body_children_add_admin_orgs_org_id_children_post.additional_properties = d
        return body_children_add_admin_orgs_org_id_children_post

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
