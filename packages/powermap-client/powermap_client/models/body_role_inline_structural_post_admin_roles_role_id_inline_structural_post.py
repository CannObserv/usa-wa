from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..types import UNSET, Unset

T = TypeVar("T", bound="BodyRoleInlineStructuralPostAdminRolesRoleIdInlineStructuralPost")


@_attrs_define
class BodyRoleInlineStructuralPostAdminRolesRoleIdInlineStructuralPost:
    """
    Attributes:
        role_type_id (str | Unset):  Default: ''.
        jurisdiction_id (str | Unset):  Default: ''.
        qualifier (str | Unset):  Default: ''.
    """

    role_type_id: str | Unset = ""
    jurisdiction_id: str | Unset = ""
    qualifier: str | Unset = ""
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        role_type_id = self.role_type_id

        jurisdiction_id = self.jurisdiction_id

        qualifier = self.qualifier

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update({})
        if role_type_id is not UNSET:
            field_dict["role_type_id"] = role_type_id
        if jurisdiction_id is not UNSET:
            field_dict["jurisdiction_id"] = jurisdiction_id
        if qualifier is not UNSET:
            field_dict["qualifier"] = qualifier

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        role_type_id = d.pop("role_type_id", UNSET)

        jurisdiction_id = d.pop("jurisdiction_id", UNSET)

        qualifier = d.pop("qualifier", UNSET)

        body_role_inline_structural_post_admin_roles_role_id_inline_structural_post = cls(
            role_type_id=role_type_id,
            jurisdiction_id=jurisdiction_id,
            qualifier=qualifier,
        )

        body_role_inline_structural_post_admin_roles_role_id_inline_structural_post.additional_properties = d
        return body_role_inline_structural_post_admin_roles_role_id_inline_structural_post

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
