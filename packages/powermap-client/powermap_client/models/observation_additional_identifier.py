from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar

from attrs import define as _attrs_define
from attrs import field as _attrs_field

T = TypeVar("T", bound="ObservationAdditionalIdentifier")


@_attrs_define
class ObservationAdditionalIdentifier:
    """An additional identifier claim to attach to the resolved entity.

    Attributes:
        identifier_type_slug (str):
        identifier_value (str):
    """

    identifier_type_slug: str
    identifier_value: str
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        identifier_type_slug = self.identifier_type_slug

        identifier_value = self.identifier_value

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "identifier_type_slug": identifier_type_slug,
                "identifier_value": identifier_value,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        identifier_type_slug = d.pop("identifier_type_slug")

        identifier_value = d.pop("identifier_value")

        observation_additional_identifier = cls(
            identifier_type_slug=identifier_type_slug,
            identifier_value=identifier_value,
        )

        observation_additional_identifier.additional_properties = d
        return observation_additional_identifier

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
