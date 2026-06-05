from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar

from attrs import define as _attrs_define
from attrs import field as _attrs_field

T = TypeVar("T", bound="BodyIdentifierEditRowPostAdminOrgsEntityIdIdentifiersIdentIdEditRowPost")


@_attrs_define
class BodyIdentifierEditRowPostAdminOrgsEntityIdIdentifiersIdentIdEditRowPost:
    """
    Attributes:
        entity_identifier_type_id (str):
        value (str):
    """

    entity_identifier_type_id: str
    value: str
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        entity_identifier_type_id = self.entity_identifier_type_id

        value = self.value

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "entity_identifier_type_id": entity_identifier_type_id,
                "value": value,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        entity_identifier_type_id = d.pop("entity_identifier_type_id")

        value = d.pop("value")

        body_identifier_edit_row_post_admin_orgs_entity_id_identifiers_ident_id_edit_row_post = cls(
            entity_identifier_type_id=entity_identifier_type_id,
            value=value,
        )

        body_identifier_edit_row_post_admin_orgs_entity_id_identifiers_ident_id_edit_row_post.additional_properties = d
        return body_identifier_edit_row_post_admin_orgs_entity_id_identifiers_ident_id_edit_row_post

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
