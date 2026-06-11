from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..types import UNSET, Unset

T = TypeVar("T", bound="EventPlaceAddress")


@_attrs_define
class EventPlaceAddress:
    """Structured address linked to an event place.

    Attributes:
        id (str):
        city (None | str | Unset):
        region (None | str | Unset):
        standardized (None | str | Unset):
        precision (None | str | Unset):
    """

    id: str
    city: None | str | Unset = UNSET
    region: None | str | Unset = UNSET
    standardized: None | str | Unset = UNSET
    precision: None | str | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        id = self.id

        city: None | str | Unset
        if isinstance(self.city, Unset):
            city = UNSET
        else:
            city = self.city

        region: None | str | Unset
        if isinstance(self.region, Unset):
            region = UNSET
        else:
            region = self.region

        standardized: None | str | Unset
        if isinstance(self.standardized, Unset):
            standardized = UNSET
        else:
            standardized = self.standardized

        precision: None | str | Unset
        if isinstance(self.precision, Unset):
            precision = UNSET
        else:
            precision = self.precision

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "id": id,
            }
        )
        if city is not UNSET:
            field_dict["city"] = city
        if region is not UNSET:
            field_dict["region"] = region
        if standardized is not UNSET:
            field_dict["standardized"] = standardized
        if precision is not UNSET:
            field_dict["precision"] = precision

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        id = d.pop("id")

        def _parse_city(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        city = _parse_city(d.pop("city", UNSET))

        def _parse_region(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        region = _parse_region(d.pop("region", UNSET))

        def _parse_standardized(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        standardized = _parse_standardized(d.pop("standardized", UNSET))

        def _parse_precision(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        precision = _parse_precision(d.pop("precision", UNSET))

        event_place_address = cls(
            id=id,
            city=city,
            region=region,
            standardized=standardized,
            precision=precision,
        )

        event_place_address.additional_properties = d
        return event_place_address

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
