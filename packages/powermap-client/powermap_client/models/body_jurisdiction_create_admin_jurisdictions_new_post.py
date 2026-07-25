from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..types import UNSET, Unset

T = TypeVar("T", bound="BodyJurisdictionCreateAdminJurisdictionsNewPost")


@_attrs_define
class BodyJurisdictionCreateAdminJurisdictionsNewPost:
    """
    Attributes:
        slug (str | Unset):  Default: ''.
        name (str | Unset):  Default: ''.
        type_id (str | Unset):  Default: ''.
        valid_from (str | Unset):  Default: ''.
        valid_until (str | Unset):  Default: ''.
        notes (str | Unset):  Default: ''.
    """

    slug: str | Unset = ""
    name: str | Unset = ""
    type_id: str | Unset = ""
    valid_from: str | Unset = ""
    valid_until: str | Unset = ""
    notes: str | Unset = ""
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        slug = self.slug

        name = self.name

        type_id = self.type_id

        valid_from = self.valid_from

        valid_until = self.valid_until

        notes = self.notes

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update({})
        if slug is not UNSET:
            field_dict["slug"] = slug
        if name is not UNSET:
            field_dict["name"] = name
        if type_id is not UNSET:
            field_dict["type_id"] = type_id
        if valid_from is not UNSET:
            field_dict["valid_from"] = valid_from
        if valid_until is not UNSET:
            field_dict["valid_until"] = valid_until
        if notes is not UNSET:
            field_dict["notes"] = notes

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        slug = d.pop("slug", UNSET)

        name = d.pop("name", UNSET)

        type_id = d.pop("type_id", UNSET)

        valid_from = d.pop("valid_from", UNSET)

        valid_until = d.pop("valid_until", UNSET)

        notes = d.pop("notes", UNSET)

        body_jurisdiction_create_admin_jurisdictions_new_post = cls(
            slug=slug,
            name=name,
            type_id=type_id,
            valid_from=valid_from,
            valid_until=valid_until,
            notes=notes,
        )

        body_jurisdiction_create_admin_jurisdictions_new_post.additional_properties = d
        return body_jurisdiction_create_admin_jurisdictions_new_post

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
