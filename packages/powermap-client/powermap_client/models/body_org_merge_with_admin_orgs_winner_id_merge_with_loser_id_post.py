from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..types import UNSET, Unset

T = TypeVar("T", bound="BodyOrgMergeWithAdminOrgsWinnerIdMergeWithLoserIdPost")


@_attrs_define
class BodyOrgMergeWithAdminOrgsWinnerIdMergeWithLoserIdPost:
    """
    Attributes:
        keep_name_ids (list[str] | Unset):
        keep_acronym_ids (list[str] | Unset):
        merge_role_pairs (list[str] | Unset):
    """

    keep_name_ids: list[str] | Unset = UNSET
    keep_acronym_ids: list[str] | Unset = UNSET
    merge_role_pairs: list[str] | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        keep_name_ids: list[str] | Unset = UNSET
        if not isinstance(self.keep_name_ids, Unset):
            keep_name_ids = self.keep_name_ids

        keep_acronym_ids: list[str] | Unset = UNSET
        if not isinstance(self.keep_acronym_ids, Unset):
            keep_acronym_ids = self.keep_acronym_ids

        merge_role_pairs: list[str] | Unset = UNSET
        if not isinstance(self.merge_role_pairs, Unset):
            merge_role_pairs = self.merge_role_pairs

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update({})
        if keep_name_ids is not UNSET:
            field_dict["keep_name_ids"] = keep_name_ids
        if keep_acronym_ids is not UNSET:
            field_dict["keep_acronym_ids"] = keep_acronym_ids
        if merge_role_pairs is not UNSET:
            field_dict["merge_role_pairs"] = merge_role_pairs

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        keep_name_ids = cast(list[str], d.pop("keep_name_ids", UNSET))

        keep_acronym_ids = cast(list[str], d.pop("keep_acronym_ids", UNSET))

        merge_role_pairs = cast(list[str], d.pop("merge_role_pairs", UNSET))

        body_org_merge_with_admin_orgs_winner_id_merge_with_loser_id_post = cls(
            keep_name_ids=keep_name_ids,
            keep_acronym_ids=keep_acronym_ids,
            merge_role_pairs=merge_role_pairs,
        )

        body_org_merge_with_admin_orgs_winner_id_merge_with_loser_id_post.additional_properties = d
        return body_org_merge_with_admin_orgs_winner_id_merge_with_loser_id_post

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
