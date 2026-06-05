from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..types import UNSET, Unset

T = TypeVar("T", bound="BodyRaInlineDatesPostAdminRoleAssignmentsRaIdInlineDatesPost")


@_attrs_define
class BodyRaInlineDatesPostAdminRoleAssignmentsRaIdInlineDatesPost:
    """
    Attributes:
        start_date (str | Unset):  Default: ''.
        end_date (str | Unset):  Default: ''.
    """

    start_date: str | Unset = ""
    end_date: str | Unset = ""
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        start_date = self.start_date

        end_date = self.end_date

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update({})
        if start_date is not UNSET:
            field_dict["start_date"] = start_date
        if end_date is not UNSET:
            field_dict["end_date"] = end_date

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        start_date = d.pop("start_date", UNSET)

        end_date = d.pop("end_date", UNSET)

        body_ra_inline_dates_post_admin_role_assignments_ra_id_inline_dates_post = cls(
            start_date=start_date,
            end_date=end_date,
        )

        body_ra_inline_dates_post_admin_role_assignments_ra_id_inline_dates_post.additional_properties = d
        return body_ra_inline_dates_post_admin_role_assignments_ra_id_inline_dates_post

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
