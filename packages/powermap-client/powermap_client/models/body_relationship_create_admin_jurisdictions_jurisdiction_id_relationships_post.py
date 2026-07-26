from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..types import UNSET, Unset

T = TypeVar("T", bound="BodyRelationshipCreateAdminJurisdictionsJurisdictionIdRelationshipsPost")


@_attrs_define
class BodyRelationshipCreateAdminJurisdictionsJurisdictionIdRelationshipsPost:
    """
    Attributes:
        target_id (str | Unset):  Default: ''.
        rel_type_id (str | Unset):  Default: ''.
        direction (str | Unset):  Default: 'outgoing'.
        valid_from (str | Unset):  Default: ''.
        valid_until (str | Unset):  Default: ''.
        notes (str | Unset):  Default: ''.
    """

    target_id: str | Unset = ""
    rel_type_id: str | Unset = ""
    direction: str | Unset = "outgoing"
    valid_from: str | Unset = ""
    valid_until: str | Unset = ""
    notes: str | Unset = ""
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        target_id = self.target_id

        rel_type_id = self.rel_type_id

        direction = self.direction

        valid_from = self.valid_from

        valid_until = self.valid_until

        notes = self.notes

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update({})
        if target_id is not UNSET:
            field_dict["target_id"] = target_id
        if rel_type_id is not UNSET:
            field_dict["rel_type_id"] = rel_type_id
        if direction is not UNSET:
            field_dict["direction"] = direction
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
        target_id = d.pop("target_id", UNSET)

        rel_type_id = d.pop("rel_type_id", UNSET)

        direction = d.pop("direction", UNSET)

        valid_from = d.pop("valid_from", UNSET)

        valid_until = d.pop("valid_until", UNSET)

        notes = d.pop("notes", UNSET)

        body_relationship_create_admin_jurisdictions_jurisdiction_id_relationships_post = cls(
            target_id=target_id,
            rel_type_id=rel_type_id,
            direction=direction,
            valid_from=valid_from,
            valid_until=valid_until,
            notes=notes,
        )

        body_relationship_create_admin_jurisdictions_jurisdiction_id_relationships_post.additional_properties = d
        return body_relationship_create_admin_jurisdictions_jurisdiction_id_relationships_post

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
