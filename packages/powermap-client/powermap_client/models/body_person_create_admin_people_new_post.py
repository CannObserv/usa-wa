from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..types import UNSET, Unset

T = TypeVar("T", bound="BodyPersonCreateAdminPeopleNewPost")


@_attrs_define
class BodyPersonCreateAdminPeopleNewPost:
    """
    Attributes:
        name (str):
        personal_pronouns (str | Unset):  Default: ''.
        notes (str | Unset):  Default: ''.
    """

    name: str
    personal_pronouns: str | Unset = ""
    notes: str | Unset = ""
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        name = self.name

        personal_pronouns = self.personal_pronouns

        notes = self.notes

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "name": name,
            }
        )
        if personal_pronouns is not UNSET:
            field_dict["personal_pronouns"] = personal_pronouns
        if notes is not UNSET:
            field_dict["notes"] = notes

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        name = d.pop("name")

        personal_pronouns = d.pop("personal_pronouns", UNSET)

        notes = d.pop("notes", UNSET)

        body_person_create_admin_people_new_post = cls(
            name=name,
            personal_pronouns=personal_pronouns,
            notes=notes,
        )

        body_person_create_admin_people_new_post.additional_properties = d
        return body_person_create_admin_people_new_post

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
