from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..models.body_contact_create_admin_people_entity_id_contacts_post_contact_type import (
    BodyContactCreateAdminPeopleEntityIdContactsPostContactType,
)
from ..types import UNSET, Unset

T = TypeVar("T", bound="BodyContactCreateAdminPeopleEntityIdContactsPost")


@_attrs_define
class BodyContactCreateAdminPeopleEntityIdContactsPost:
    """
    Attributes:
        contact_type (BodyContactCreateAdminPeopleEntityIdContactsPostContactType):
        value (str):
        display_label (str | Unset):  Default: ''.
    """

    contact_type: BodyContactCreateAdminPeopleEntityIdContactsPostContactType
    value: str
    display_label: str | Unset = ""
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        contact_type = self.contact_type.value

        value = self.value

        display_label = self.display_label

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "contact_type": contact_type,
                "value": value,
            }
        )
        if display_label is not UNSET:
            field_dict["display_label"] = display_label

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        contact_type = BodyContactCreateAdminPeopleEntityIdContactsPostContactType(d.pop("contact_type"))

        value = d.pop("value")

        display_label = d.pop("display_label", UNSET)

        body_contact_create_admin_people_entity_id_contacts_post = cls(
            contact_type=contact_type,
            value=value,
            display_label=display_label,
        )

        body_contact_create_admin_people_entity_id_contacts_post.additional_properties = d
        return body_contact_create_admin_people_entity_id_contacts_post

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
