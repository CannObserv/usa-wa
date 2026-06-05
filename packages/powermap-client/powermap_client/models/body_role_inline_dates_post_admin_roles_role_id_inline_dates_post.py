from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..types import UNSET, Unset

T = TypeVar("T", bound="BodyRoleInlineDatesPostAdminRolesRoleIdInlineDatesPost")


@_attrs_define
class BodyRoleInlineDatesPostAdminRolesRoleIdInlineDatesPost:
    """
    Attributes:
        established_on (str | Unset):  Default: ''.
        abolished_on (str | Unset):  Default: ''.
    """

    established_on: str | Unset = ""
    abolished_on: str | Unset = ""
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        established_on = self.established_on

        abolished_on = self.abolished_on

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update({})
        if established_on is not UNSET:
            field_dict["established_on"] = established_on
        if abolished_on is not UNSET:
            field_dict["abolished_on"] = abolished_on

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        established_on = d.pop("established_on", UNSET)

        abolished_on = d.pop("abolished_on", UNSET)

        body_role_inline_dates_post_admin_roles_role_id_inline_dates_post = cls(
            established_on=established_on,
            abolished_on=abolished_on,
        )

        body_role_inline_dates_post_admin_roles_role_id_inline_dates_post.additional_properties = d
        return body_role_inline_dates_post_admin_roles_role_id_inline_dates_post

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
