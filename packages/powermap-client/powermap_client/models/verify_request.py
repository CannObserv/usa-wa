from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field

T = TypeVar("T", bound="VerifyRequest")


@_attrs_define
class VerifyRequest:
    """Request body for POST /api/v1/people/verify — closed-set verification (#299).

    Scores the query embedding against a declared candidate set instead of the
    global top-K. Duplicate ``person_ids`` are deduped (first occurrence wins);
    the cap keeps the exact per-person scan bounded while fitting a
    legislature-scale roster in one call (#310: 25 → 500).

        Attributes:
            model_id (str):
            embedding (list[float]):
            person_ids (list[str]):
    """

    model_id: str
    embedding: list[float]
    person_ids: list[str]
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        model_id = self.model_id

        embedding = self.embedding

        person_ids = self.person_ids

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "model_id": model_id,
                "embedding": embedding,
                "person_ids": person_ids,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        model_id = d.pop("model_id")

        embedding = cast(list[float], d.pop("embedding"))

        person_ids = cast(list[str], d.pop("person_ids"))

        verify_request = cls(
            model_id=model_id,
            embedding=embedding,
            person_ids=person_ids,
        )

        verify_request.additional_properties = d
        return verify_request

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
