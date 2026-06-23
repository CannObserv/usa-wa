from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..models.change_item_change_kind import ChangeItemChangeKind
from ..models.change_item_entity_type import ChangeItemEntityType
from ..types import UNSET, Unset

T = TypeVar("T", bound="ChangeItem")


@_attrs_define
class ChangeItem:
    """A single entry in the change feed — updated or deleted entity.

    Attributes:
        seq_id (int):
        entity_type (ChangeItemEntityType):
        entity_id (str):
        changed_at (str):
        change_kind (ChangeItemChangeKind):
        merged_into (None | str | Unset):
    """

    seq_id: int
    entity_type: ChangeItemEntityType
    entity_id: str
    changed_at: str
    change_kind: ChangeItemChangeKind
    merged_into: None | str | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        seq_id = self.seq_id

        entity_type = self.entity_type.value

        entity_id = self.entity_id

        changed_at = self.changed_at

        change_kind = self.change_kind.value

        merged_into: None | str | Unset
        if isinstance(self.merged_into, Unset):
            merged_into = UNSET
        else:
            merged_into = self.merged_into

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "seq_id": seq_id,
                "entity_type": entity_type,
                "entity_id": entity_id,
                "changed_at": changed_at,
                "change_kind": change_kind,
            }
        )
        if merged_into is not UNSET:
            field_dict["merged_into"] = merged_into

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        seq_id = d.pop("seq_id")

        entity_type = ChangeItemEntityType(d.pop("entity_type"))

        entity_id = d.pop("entity_id")

        changed_at = d.pop("changed_at")

        change_kind = ChangeItemChangeKind(d.pop("change_kind"))

        def _parse_merged_into(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        merged_into = _parse_merged_into(d.pop("merged_into", UNSET))

        change_item = cls(
            seq_id=seq_id,
            entity_type=entity_type,
            entity_id=entity_id,
            changed_at=changed_at,
            change_kind=change_kind,
            merged_into=merged_into,
        )

        change_item.additional_properties = d
        return change_item

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
