from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar

from attrs import define as _attrs_define
from attrs import field as _attrs_field

if TYPE_CHECKING:
    from ..models.subscription_item import SubscriptionItem
    from ..models.subscription_list_meta import SubscriptionListMeta


T = TypeVar("T", bound="SubscriptionListResponse")


@_attrs_define
class SubscriptionListResponse:
    """Response envelope for GET /api/v1/subscriptions.

    Attributes:
        data (list[SubscriptionItem]):
        meta (SubscriptionListMeta): Pagination metadata for subscription list.
    """

    data: list[SubscriptionItem]
    meta: SubscriptionListMeta
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        data = []
        for data_item_data in self.data:
            data_item = data_item_data.to_dict()
            data.append(data_item)

        meta = self.meta.to_dict()

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "data": data,
                "meta": meta,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.subscription_item import SubscriptionItem
        from ..models.subscription_list_meta import SubscriptionListMeta

        d = dict(src_dict)
        data = []
        _data = d.pop("data")
        for data_item_data in _data:
            data_item = SubscriptionItem.from_dict(data_item_data)

            data.append(data_item)

        meta = SubscriptionListMeta.from_dict(d.pop("meta"))

        subscription_list_response = cls(
            data=data,
            meta=meta,
        )

        subscription_list_response.additional_properties = d
        return subscription_list_response

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
