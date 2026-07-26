from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar

from attrs import define as _attrs_define
from attrs import field as _attrs_field

if TYPE_CHECKING:
    from ..models.embedding_presence_result import EmbeddingPresenceResult


T = TypeVar("T", bound="EmbeddingPresenceResponse")


@_attrs_define
class EmbeddingPresenceResponse:
    """Response envelope for POST /api/v1/people/embeddings/presence — results
    in request order, duplicates deduped (first occurrence wins).

        Attributes:
            results (list[EmbeddingPresenceResult]):
    """

    results: list[EmbeddingPresenceResult]
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        results = []
        for results_item_data in self.results:
            results_item = results_item_data.to_dict()
            results.append(results_item)

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "results": results,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.embedding_presence_result import EmbeddingPresenceResult

        d = dict(src_dict)
        results = []
        _results = d.pop("results")
        for results_item_data in _results:
            results_item = EmbeddingPresenceResult.from_dict(results_item_data)

            results.append(results_item)

        embedding_presence_response = cls(
            results=results,
        )

        embedding_presence_response.additional_properties = d
        return embedding_presence_response

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
