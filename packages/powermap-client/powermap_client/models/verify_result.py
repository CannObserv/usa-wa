from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field

T = TypeVar("T", bound="VerifyResult")


@_attrs_define
class VerifyResult:
    """Per-candidate verification score.

    One result per requested person_id, always — a person with no active
    embeddings under the model (or an unknown/archived person id) comes back
    with ``similarity: null, embedding_id: null, n_embeddings: 0`` so callers
    can distinguish absence from a low score.

        Attributes:
            person_id (str):
            similarity (float | None):
            embedding_id (None | str):
            n_embeddings (int):
    """

    person_id: str
    similarity: float | None
    embedding_id: None | str
    n_embeddings: int
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        person_id = self.person_id

        similarity: float | None
        similarity = self.similarity

        embedding_id: None | str
        embedding_id = self.embedding_id

        n_embeddings = self.n_embeddings

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "person_id": person_id,
                "similarity": similarity,
                "embedding_id": embedding_id,
                "n_embeddings": n_embeddings,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        person_id = d.pop("person_id")

        def _parse_similarity(data: object) -> float | None:
            if data is None:
                return data
            return cast(float | None, data)

        similarity = _parse_similarity(d.pop("similarity"))

        def _parse_embedding_id(data: object) -> None | str:
            if data is None:
                return data
            return cast(None | str, data)

        embedding_id = _parse_embedding_id(d.pop("embedding_id"))

        n_embeddings = d.pop("n_embeddings")

        verify_result = cls(
            person_id=person_id,
            similarity=similarity,
            embedding_id=embedding_id,
            n_embeddings=n_embeddings,
        )

        verify_result.additional_properties = d
        return verify_result

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
