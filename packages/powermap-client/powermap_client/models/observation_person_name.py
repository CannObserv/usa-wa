from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..models.observation_person_name_name_type import ObservationPersonNameNameType
from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.observation_person_name_parts import ObservationPersonNameParts


T = TypeVar("T", bound="ObservationPersonName")


@_attrs_define
class ObservationPersonName:
    """A name claim included in a person observation.

    Attributes:
        name (str):
        name_type (ObservationPersonNameNameType | Unset):  Default: ObservationPersonNameNameType.LEGAL.
        locale (None | str | Unset):
        script (None | str | Unset):
        sort_as (None | str | Unset):
        parts (None | ObservationPersonNameParts | Unset):
        is_canonical (bool | Unset):  Default: False.
    """

    name: str
    name_type: ObservationPersonNameNameType | Unset = ObservationPersonNameNameType.LEGAL
    locale: None | str | Unset = UNSET
    script: None | str | Unset = UNSET
    sort_as: None | str | Unset = UNSET
    parts: None | ObservationPersonNameParts | Unset = UNSET
    is_canonical: bool | Unset = False
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        from ..models.observation_person_name_parts import ObservationPersonNameParts

        name = self.name

        name_type: str | Unset = UNSET
        if not isinstance(self.name_type, Unset):
            name_type = self.name_type.value

        locale: None | str | Unset
        if isinstance(self.locale, Unset):
            locale = UNSET
        else:
            locale = self.locale

        script: None | str | Unset
        if isinstance(self.script, Unset):
            script = UNSET
        else:
            script = self.script

        sort_as: None | str | Unset
        if isinstance(self.sort_as, Unset):
            sort_as = UNSET
        else:
            sort_as = self.sort_as

        parts: dict[str, Any] | None | Unset
        if isinstance(self.parts, Unset):
            parts = UNSET
        elif isinstance(self.parts, ObservationPersonNameParts):
            parts = self.parts.to_dict()
        else:
            parts = self.parts

        is_canonical = self.is_canonical

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "name": name,
            }
        )
        if name_type is not UNSET:
            field_dict["name_type"] = name_type
        if locale is not UNSET:
            field_dict["locale"] = locale
        if script is not UNSET:
            field_dict["script"] = script
        if sort_as is not UNSET:
            field_dict["sort_as"] = sort_as
        if parts is not UNSET:
            field_dict["parts"] = parts
        if is_canonical is not UNSET:
            field_dict["is_canonical"] = is_canonical

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.observation_person_name_parts import ObservationPersonNameParts

        d = dict(src_dict)
        name = d.pop("name")

        _name_type = d.pop("name_type", UNSET)
        name_type: ObservationPersonNameNameType | Unset
        if isinstance(_name_type, Unset):
            name_type = UNSET
        else:
            name_type = ObservationPersonNameNameType(_name_type)

        def _parse_locale(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        locale = _parse_locale(d.pop("locale", UNSET))

        def _parse_script(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        script = _parse_script(d.pop("script", UNSET))

        def _parse_sort_as(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        sort_as = _parse_sort_as(d.pop("sort_as", UNSET))

        def _parse_parts(data: object) -> None | ObservationPersonNameParts | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, dict):
                    raise TypeError()
                parts_type_0 = ObservationPersonNameParts.from_dict(data)

                return parts_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(None | ObservationPersonNameParts | Unset, data)

        parts = _parse_parts(d.pop("parts", UNSET))

        is_canonical = d.pop("is_canonical", UNSET)

        observation_person_name = cls(
            name=name,
            name_type=name_type,
            locale=locale,
            script=script,
            sort_as=sort_as,
            parts=parts,
            is_canonical=is_canonical,
        )

        observation_person_name.additional_properties = d
        return observation_person_name

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
