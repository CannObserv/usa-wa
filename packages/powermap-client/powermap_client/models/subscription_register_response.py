from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field

T = TypeVar("T", bound="SubscriptionRegisterResponse")


@_attrs_define
class SubscriptionRegisterResponse:
    """Response for POST /api/v1/subscriptions.

    Attributes:
        registered (int):
        already_subscribed (int):
        not_found (list[str]):
    """

    registered: int
    already_subscribed: int
    not_found: list[str]
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        registered = self.registered

        already_subscribed = self.already_subscribed

        not_found = self.not_found

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "registered": registered,
                "already_subscribed": already_subscribed,
                "not_found": not_found,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        registered = d.pop("registered")

        already_subscribed = d.pop("already_subscribed")

        not_found = cast(list[str], d.pop("not_found"))

        subscription_register_response = cls(
            registered=registered,
            already_subscribed=already_subscribed,
            not_found=not_found,
        )

        subscription_register_response.additional_properties = d
        return subscription_register_response

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
