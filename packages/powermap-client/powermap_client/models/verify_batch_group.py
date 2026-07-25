from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar

from attrs import define as _attrs_define
from attrs import field as _attrs_field

if TYPE_CHECKING:
    from ..models.verify_result import VerifyResult


T = TypeVar("T", bound="VerifyBatchGroup")


@_attrs_define
class VerifyBatchGroup:
    """Per-embedding result group — ``embedding_index`` is the position of the
    query embedding in the request's ``embeddings`` list; ``results`` carries
    the per-candidate scores with the same semantics as ``VerifyResult``.

        Attributes:
            embedding_index (int):
            results (list[VerifyResult]):
    """

    embedding_index: int
    results: list[VerifyResult]
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        embedding_index = self.embedding_index

        results = []
        for results_item_data in self.results:
            results_item = results_item_data.to_dict()
            results.append(results_item)

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "embedding_index": embedding_index,
                "results": results,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.verify_result import VerifyResult

        d = dict(src_dict)
        embedding_index = d.pop("embedding_index")

        results = []
        _results = d.pop("results")
        for results_item_data in _results:
            results_item = VerifyResult.from_dict(results_item_data)

            results.append(results_item)

        verify_batch_group = cls(
            embedding_index=embedding_index,
            results=results,
        )

        verify_batch_group.additional_properties = d
        return verify_batch_group

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
