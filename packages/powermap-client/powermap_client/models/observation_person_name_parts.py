from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..models.observation_person_name_parts_primary_identifier_type_0 import (
    ObservationPersonNamePartsPrimaryIdentifierType0,
)
from ..types import UNSET, Unset

T = TypeVar("T", bound="ObservationPersonNameParts")


@_attrs_define
class ObservationPersonNameParts:
    """Structured name parts supplied by upstream source (pre-parsed, not auto-decomposed).

    Attributes:
        given_names (list[str] | Unset):
        family_names (list[str] | Unset):
        additional_names (list[str] | Unset):
        honorific_prefix (None | str | Unset):
        honorific_suffix (None | str | Unset):
        primary_identifier (None | ObservationPersonNamePartsPrimaryIdentifierType0 | Unset):
    """

    given_names: list[str] | Unset = UNSET
    family_names: list[str] | Unset = UNSET
    additional_names: list[str] | Unset = UNSET
    honorific_prefix: None | str | Unset = UNSET
    honorific_suffix: None | str | Unset = UNSET
    primary_identifier: None | ObservationPersonNamePartsPrimaryIdentifierType0 | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        given_names: list[str] | Unset = UNSET
        if not isinstance(self.given_names, Unset):
            given_names = self.given_names

        family_names: list[str] | Unset = UNSET
        if not isinstance(self.family_names, Unset):
            family_names = self.family_names

        additional_names: list[str] | Unset = UNSET
        if not isinstance(self.additional_names, Unset):
            additional_names = self.additional_names

        honorific_prefix: None | str | Unset
        if isinstance(self.honorific_prefix, Unset):
            honorific_prefix = UNSET
        else:
            honorific_prefix = self.honorific_prefix

        honorific_suffix: None | str | Unset
        if isinstance(self.honorific_suffix, Unset):
            honorific_suffix = UNSET
        else:
            honorific_suffix = self.honorific_suffix

        primary_identifier: None | str | Unset
        if isinstance(self.primary_identifier, Unset):
            primary_identifier = UNSET
        elif isinstance(self.primary_identifier, ObservationPersonNamePartsPrimaryIdentifierType0):
            primary_identifier = self.primary_identifier.value
        else:
            primary_identifier = self.primary_identifier

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update({})
        if given_names is not UNSET:
            field_dict["given_names"] = given_names
        if family_names is not UNSET:
            field_dict["family_names"] = family_names
        if additional_names is not UNSET:
            field_dict["additional_names"] = additional_names
        if honorific_prefix is not UNSET:
            field_dict["honorific_prefix"] = honorific_prefix
        if honorific_suffix is not UNSET:
            field_dict["honorific_suffix"] = honorific_suffix
        if primary_identifier is not UNSET:
            field_dict["primary_identifier"] = primary_identifier

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        given_names = cast(list[str], d.pop("given_names", UNSET))

        family_names = cast(list[str], d.pop("family_names", UNSET))

        additional_names = cast(list[str], d.pop("additional_names", UNSET))

        def _parse_honorific_prefix(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        honorific_prefix = _parse_honorific_prefix(d.pop("honorific_prefix", UNSET))

        def _parse_honorific_suffix(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        honorific_suffix = _parse_honorific_suffix(d.pop("honorific_suffix", UNSET))

        def _parse_primary_identifier(data: object) -> None | ObservationPersonNamePartsPrimaryIdentifierType0 | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, str):
                    raise TypeError()
                primary_identifier_type_0 = ObservationPersonNamePartsPrimaryIdentifierType0(data)

                return primary_identifier_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(None | ObservationPersonNamePartsPrimaryIdentifierType0 | Unset, data)

        primary_identifier = _parse_primary_identifier(d.pop("primary_identifier", UNSET))

        observation_person_name_parts = cls(
            given_names=given_names,
            family_names=family_names,
            additional_names=additional_names,
            honorific_prefix=honorific_prefix,
            honorific_suffix=honorific_suffix,
            primary_identifier=primary_identifier,
        )

        observation_person_name_parts.additional_properties = d
        return observation_person_name_parts

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
