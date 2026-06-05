from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar

from attrs import define as _attrs_define
from attrs import field as _attrs_field

T = TypeVar("T", bound="ChangeMeta")


@_attrs_define
class ChangeMeta:
    """Pagination metadata for the change feed.

    Attributes:
        limit (int):
        count (int):
        has_more (bool):
        next_since (str):
    """

    limit: int
    count: int
    has_more: bool
    next_since: str
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        limit = self.limit

        count = self.count

        has_more = self.has_more

        next_since = self.next_since

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "limit": limit,
                "count": count,
                "has_more": has_more,
                "next_since": next_since,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        limit = d.pop("limit")

        count = d.pop("count")

        has_more = d.pop("has_more")

        next_since = d.pop("next_since")

        change_meta = cls(
            limit=limit,
            count=count,
            has_more=has_more,
            next_since=next_since,
        )

        change_meta.additional_properties = d
        return change_meta

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
