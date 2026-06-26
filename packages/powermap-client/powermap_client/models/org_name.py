from __future__ import annotations

import datetime
from collections.abc import Mapping
from typing import Any, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..types import UNSET, Unset

T = TypeVar("T", bound="OrgName")


@_attrs_define
class OrgName:
    """A single name variant for an organization.

    Attributes:
        id (str):
        name (str):
        name_type (str):
        is_canonical (bool):
        effective_start (datetime.date | None | Unset):
        effective_end (datetime.date | None | Unset):
    """

    id: str
    name: str
    name_type: str
    is_canonical: bool
    effective_start: datetime.date | None | Unset = UNSET
    effective_end: datetime.date | None | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        id = self.id

        name = self.name

        name_type = self.name_type

        is_canonical = self.is_canonical

        effective_start: None | str | Unset
        if isinstance(self.effective_start, Unset):
            effective_start = UNSET
        elif isinstance(self.effective_start, datetime.date):
            effective_start = self.effective_start.isoformat()
        else:
            effective_start = self.effective_start

        effective_end: None | str | Unset
        if isinstance(self.effective_end, Unset):
            effective_end = UNSET
        elif isinstance(self.effective_end, datetime.date):
            effective_end = self.effective_end.isoformat()
        else:
            effective_end = self.effective_end

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "id": id,
                "name": name,
                "name_type": name_type,
                "is_canonical": is_canonical,
            }
        )
        if effective_start is not UNSET:
            field_dict["effective_start"] = effective_start
        if effective_end is not UNSET:
            field_dict["effective_end"] = effective_end

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        id = d.pop("id")

        name = d.pop("name")

        name_type = d.pop("name_type")

        is_canonical = d.pop("is_canonical")

        def _parse_effective_start(data: object) -> datetime.date | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, str):
                    raise TypeError()
                effective_start_type_0 = datetime.date.fromisoformat(data)

                return effective_start_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(datetime.date | None | Unset, data)

        effective_start = _parse_effective_start(d.pop("effective_start", UNSET))

        def _parse_effective_end(data: object) -> datetime.date | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, str):
                    raise TypeError()
                effective_end_type_0 = datetime.date.fromisoformat(data)

                return effective_end_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(datetime.date | None | Unset, data)

        effective_end = _parse_effective_end(d.pop("effective_end", UNSET))

        org_name = cls(
            id=id,
            name=name,
            name_type=name_type,
            is_canonical=is_canonical,
            effective_start=effective_start,
            effective_end=effective_end,
        )

        org_name.additional_properties = d
        return org_name

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
