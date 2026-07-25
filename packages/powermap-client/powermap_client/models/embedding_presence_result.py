from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar

from attrs import define as _attrs_define
from attrs import field as _attrs_field

T = TypeVar("T", bound="EmbeddingPresenceResult")


@_attrs_define
class EmbeddingPresenceResult:
    """Active-enrollment count for one requested person_id — 0 for a person
    with no active embeddings under the model (or an unknown/archived id).

        Attributes:
            person_id (str):
            n_embeddings (int):
    """

    person_id: str
    n_embeddings: int
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        person_id = self.person_id

        n_embeddings = self.n_embeddings

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "person_id": person_id,
                "n_embeddings": n_embeddings,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        person_id = d.pop("person_id")

        n_embeddings = d.pop("n_embeddings")

        embedding_presence_result = cls(
            person_id=person_id,
            n_embeddings=n_embeddings,
        )

        embedding_presence_result.additional_properties = d
        return embedding_presence_result

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
