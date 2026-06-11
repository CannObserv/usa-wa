from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field

T = TypeVar("T", bound="EmbeddingWriteResponse")


@_attrs_define
class EmbeddingWriteResponse:
    """Response for a successful embedding write (new or idempotent duplicate).

    Attributes:
        embedding_id (str):
        person_id (str):
        created_at (None | str):
    """

    embedding_id: str
    person_id: str
    created_at: None | str
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        embedding_id = self.embedding_id

        person_id = self.person_id

        created_at: None | str
        created_at = self.created_at

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "embedding_id": embedding_id,
                "person_id": person_id,
                "created_at": created_at,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        embedding_id = d.pop("embedding_id")

        person_id = d.pop("person_id")

        def _parse_created_at(data: object) -> None | str:
            if data is None:
                return data
            return cast(None | str, data)

        created_at = _parse_created_at(d.pop("created_at"))

        embedding_write_response = cls(
            embedding_id=embedding_id,
            person_id=person_id,
            created_at=created_at,
        )

        embedding_write_response.additional_properties = d
        return embedding_write_response

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
