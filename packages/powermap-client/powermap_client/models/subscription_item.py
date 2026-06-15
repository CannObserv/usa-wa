from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..models.subscription_item_entity_type import SubscriptionItemEntityType

T = TypeVar("T", bound="SubscriptionItem")


@_attrs_define
class SubscriptionItem:
    """A single subscription row returned by GET /api/v1/subscriptions.

    Attributes:
        entity_id (str):
        entity_type (SubscriptionItemEntityType):
        created_at (str):
    """

    entity_id: str
    entity_type: SubscriptionItemEntityType
    created_at: str
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        entity_id = self.entity_id

        entity_type = self.entity_type.value

        created_at = self.created_at

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "entity_id": entity_id,
                "entity_type": entity_type,
                "created_at": created_at,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        entity_id = d.pop("entity_id")

        entity_type = SubscriptionItemEntityType(d.pop("entity_type"))

        created_at = d.pop("created_at")

        subscription_item = cls(
            entity_id=entity_id,
            entity_type=entity_type,
            created_at=created_at,
        )

        subscription_item.additional_properties = d
        return subscription_item

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
