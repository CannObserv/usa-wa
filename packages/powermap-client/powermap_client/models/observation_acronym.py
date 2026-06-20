from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..types import UNSET, Unset

T = TypeVar("T", bound="ObservationAcronym")


@_attrs_define
class ObservationAcronym:
    """An acronym claim included in an org observation.

    Attributes:
        acronym (str):
        is_canonical (bool | Unset):  Default: False.
    """

    acronym: str
    is_canonical: bool | Unset = False
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        acronym = self.acronym

        is_canonical = self.is_canonical

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "acronym": acronym,
            }
        )
        if is_canonical is not UNSET:
            field_dict["is_canonical"] = is_canonical

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        acronym = d.pop("acronym")

        is_canonical = d.pop("is_canonical", UNSET)

        observation_acronym = cls(
            acronym=acronym,
            is_canonical=is_canonical,
        )

        observation_acronym.additional_properties = d
        return observation_acronym

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
