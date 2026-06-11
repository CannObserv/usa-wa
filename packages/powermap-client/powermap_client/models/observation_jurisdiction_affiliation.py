from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar

from attrs import define as _attrs_define
from attrs import field as _attrs_field

T = TypeVar("T", bound="ObservationJurisdictionAffiliation")


@_attrs_define
class ObservationJurisdictionAffiliation:
    """A jurisdiction affiliation claim in an observation payload.

    Attributes:
        jurisdiction_id (str):
        affiliation_type_slug (str):
    """

    jurisdiction_id: str
    affiliation_type_slug: str
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        jurisdiction_id = self.jurisdiction_id

        affiliation_type_slug = self.affiliation_type_slug

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "jurisdiction_id": jurisdiction_id,
                "affiliation_type_slug": affiliation_type_slug,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        jurisdiction_id = d.pop("jurisdiction_id")

        affiliation_type_slug = d.pop("affiliation_type_slug")

        observation_jurisdiction_affiliation = cls(
            jurisdiction_id=jurisdiction_id,
            affiliation_type_slug=affiliation_type_slug,
        )

        observation_jurisdiction_affiliation.additional_properties = d
        return observation_jurisdiction_affiliation

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
