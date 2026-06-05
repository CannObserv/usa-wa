from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..types import UNSET, Unset

T = TypeVar("T", bound="BodyRaInlineIsCurrentAdminRoleAssignmentsRaIdInlineIsCurrentPost")


@_attrs_define
class BodyRaInlineIsCurrentAdminRoleAssignmentsRaIdInlineIsCurrentPost:
    """
    Attributes:
        is_current (str | Unset):  Default: ''.
    """

    is_current: str | Unset = ""
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        is_current = self.is_current

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update({})
        if is_current is not UNSET:
            field_dict["is_current"] = is_current

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        is_current = d.pop("is_current", UNSET)

        body_ra_inline_is_current_admin_role_assignments_ra_id_inline_is_current_post = cls(
            is_current=is_current,
        )

        body_ra_inline_is_current_admin_role_assignments_ra_id_inline_is_current_post.additional_properties = d
        return body_ra_inline_is_current_admin_role_assignments_ra_id_inline_is_current_post

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
