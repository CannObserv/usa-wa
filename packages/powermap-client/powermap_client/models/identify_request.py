from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..types import UNSET, Unset

T = TypeVar("T", bound="IdentifyRequest")


@_attrs_define
class IdentifyRequest:
    """Request body for POST /api/v1/people/identify.

    Attributes:
        model_id (str):
        embedding (list[float]):
        top_k (int | Unset):  Default: 5.
    """

    model_id: str
    embedding: list[float]
    top_k: int | Unset = 5
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        model_id = self.model_id

        embedding = self.embedding

        top_k = self.top_k

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "model_id": model_id,
                "embedding": embedding,
            }
        )
        if top_k is not UNSET:
            field_dict["top_k"] = top_k

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        model_id = d.pop("model_id")

        embedding = cast(list[float], d.pop("embedding"))

        top_k = d.pop("top_k", UNSET)

        identify_request = cls(
            model_id=model_id,
            embedding=embedding,
            top_k=top_k,
        )

        identify_request.additional_properties = d
        return identify_request

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
