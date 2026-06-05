from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..types import UNSET, Unset

T = TypeVar("T", bound="BodyOrgCreateAdminOrgsNewPost")


@_attrs_define
class BodyOrgCreateAdminOrgsNewPost:
    """
    Attributes:
        name (str | Unset):  Default: ''.
        acronym (str | Unset):  Default: ''.
        active (str | Unset):  Default: ''.
        parent_id (str | Unset):  Default: ''.
        notes (str | Unset):  Default: ''.
    """

    name: str | Unset = ""
    acronym: str | Unset = ""
    active: str | Unset = ""
    parent_id: str | Unset = ""
    notes: str | Unset = ""
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        name = self.name

        acronym = self.acronym

        active = self.active

        parent_id = self.parent_id

        notes = self.notes

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update({})
        if name is not UNSET:
            field_dict["name"] = name
        if acronym is not UNSET:
            field_dict["acronym"] = acronym
        if active is not UNSET:
            field_dict["active"] = active
        if parent_id is not UNSET:
            field_dict["parent_id"] = parent_id
        if notes is not UNSET:
            field_dict["notes"] = notes

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        name = d.pop("name", UNSET)

        acronym = d.pop("acronym", UNSET)

        active = d.pop("active", UNSET)

        parent_id = d.pop("parent_id", UNSET)

        notes = d.pop("notes", UNSET)

        body_org_create_admin_orgs_new_post = cls(
            name=name,
            acronym=acronym,
            active=active,
            parent_id=parent_id,
            notes=notes,
        )

        body_org_create_admin_orgs_new_post.additional_properties = d
        return body_org_create_admin_orgs_new_post

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
