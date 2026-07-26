from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field

T = TypeVar("T", bound="VerifyBatchRequest")


@_attrs_define
class VerifyBatchRequest:
    """Request body for POST /api/v1/people/verify-batch (#310).

    Scores N query embeddings against one declared candidate set in a single
    call — collapses the per-centroid verify loop for archival-scale jobs.
    Duplicate ``person_ids`` are deduped (first occurrence wins).  The caps
    bound the exact scoring product at 50 × 500 = 25k pairs.

        Attributes:
            model_id (str):
            embeddings (list[list[float]]):
            person_ids (list[str]):
    """

    model_id: str
    embeddings: list[list[float]]
    person_ids: list[str]
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        model_id = self.model_id

        embeddings = []
        for embeddings_item_data in self.embeddings:
            embeddings_item = embeddings_item_data

            embeddings.append(embeddings_item)

        person_ids = self.person_ids

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "model_id": model_id,
                "embeddings": embeddings,
                "person_ids": person_ids,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        model_id = d.pop("model_id")

        embeddings = []
        _embeddings = d.pop("embeddings")
        for embeddings_item_data in _embeddings:
            embeddings_item = cast(list[float], embeddings_item_data)

            embeddings.append(embeddings_item)

        person_ids = cast(list[str], d.pop("person_ids"))

        verify_batch_request = cls(
            model_id=model_id,
            embeddings=embeddings,
            person_ids=person_ids,
        )

        verify_batch_request.additional_properties = d
        return verify_batch_request

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
