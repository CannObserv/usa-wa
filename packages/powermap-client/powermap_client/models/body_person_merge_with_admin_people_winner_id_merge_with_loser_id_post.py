from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..types import UNSET, Unset

T = TypeVar("T", bound="BodyPersonMergeWithAdminPeopleWinnerIdMergeWithLoserIdPost")


@_attrs_define
class BodyPersonMergeWithAdminPeopleWinnerIdMergeWithLoserIdPost:
    """
    Attributes:
        keep_name_ids (list[str] | Unset):
        return_to (str | Unset):  Default: 'detail'.
    """

    keep_name_ids: list[str] | Unset = UNSET
    return_to: str | Unset = "detail"
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        keep_name_ids: list[str] | Unset = UNSET
        if not isinstance(self.keep_name_ids, Unset):
            keep_name_ids = self.keep_name_ids

        return_to = self.return_to

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update({})
        if keep_name_ids is not UNSET:
            field_dict["keep_name_ids"] = keep_name_ids
        if return_to is not UNSET:
            field_dict["return_to"] = return_to

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        keep_name_ids = cast(list[str], d.pop("keep_name_ids", UNSET))

        return_to = d.pop("return_to", UNSET)

        body_person_merge_with_admin_people_winner_id_merge_with_loser_id_post = cls(
            keep_name_ids=keep_name_ids,
            return_to=return_to,
        )

        body_person_merge_with_admin_people_winner_id_merge_with_loser_id_post.additional_properties = d
        return body_person_merge_with_admin_people_winner_id_merge_with_loser_id_post

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
