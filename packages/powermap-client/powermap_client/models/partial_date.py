from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..types import UNSET, Unset

T = TypeVar("T", bound="PartialDate")


@_attrs_define
class PartialDate:
    """Partial date/time with explicit precision.

    Attributes:
        year (int | None | Unset):
        month (int | None | Unset):
        day (int | None | Unset):
        hour (int | None | Unset):
        minute (int | None | Unset):
        second (int | None | Unset):
        at (None | str | Unset):
    """

    year: int | None | Unset = UNSET
    month: int | None | Unset = UNSET
    day: int | None | Unset = UNSET
    hour: int | None | Unset = UNSET
    minute: int | None | Unset = UNSET
    second: int | None | Unset = UNSET
    at: None | str | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        year: int | None | Unset
        if isinstance(self.year, Unset):
            year = UNSET
        else:
            year = self.year

        month: int | None | Unset
        if isinstance(self.month, Unset):
            month = UNSET
        else:
            month = self.month

        day: int | None | Unset
        if isinstance(self.day, Unset):
            day = UNSET
        else:
            day = self.day

        hour: int | None | Unset
        if isinstance(self.hour, Unset):
            hour = UNSET
        else:
            hour = self.hour

        minute: int | None | Unset
        if isinstance(self.minute, Unset):
            minute = UNSET
        else:
            minute = self.minute

        second: int | None | Unset
        if isinstance(self.second, Unset):
            second = UNSET
        else:
            second = self.second

        at: None | str | Unset
        if isinstance(self.at, Unset):
            at = UNSET
        else:
            at = self.at

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update({})
        if year is not UNSET:
            field_dict["year"] = year
        if month is not UNSET:
            field_dict["month"] = month
        if day is not UNSET:
            field_dict["day"] = day
        if hour is not UNSET:
            field_dict["hour"] = hour
        if minute is not UNSET:
            field_dict["minute"] = minute
        if second is not UNSET:
            field_dict["second"] = second
        if at is not UNSET:
            field_dict["at"] = at

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)

        def _parse_year(data: object) -> int | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(int | None | Unset, data)

        year = _parse_year(d.pop("year", UNSET))

        def _parse_month(data: object) -> int | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(int | None | Unset, data)

        month = _parse_month(d.pop("month", UNSET))

        def _parse_day(data: object) -> int | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(int | None | Unset, data)

        day = _parse_day(d.pop("day", UNSET))

        def _parse_hour(data: object) -> int | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(int | None | Unset, data)

        hour = _parse_hour(d.pop("hour", UNSET))

        def _parse_minute(data: object) -> int | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(int | None | Unset, data)

        minute = _parse_minute(d.pop("minute", UNSET))

        def _parse_second(data: object) -> int | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(int | None | Unset, data)

        second = _parse_second(d.pop("second", UNSET))

        def _parse_at(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        at = _parse_at(d.pop("at", UNSET))

        partial_date = cls(
            year=year,
            month=month,
            day=day,
            hour=hour,
            minute=minute,
            second=second,
            at=at,
        )

        partial_date.additional_properties = d
        return partial_date

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
