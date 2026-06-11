from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field

T = TypeVar("T", bound="IdentifyMatch")


@_attrs_define
class IdentifyMatch:
    """A single candidate match returned by the identify endpoint.

    Attributes:
        person_id (str):
        person_name (None | str):
        similarity (float):
        embedding_id (str):
        source_job_id (str):
        recorded_at (None | str):
    """

    person_id: str
    person_name: None | str
    similarity: float
    embedding_id: str
    source_job_id: str
    recorded_at: None | str
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        person_id = self.person_id

        person_name: None | str
        person_name = self.person_name

        similarity = self.similarity

        embedding_id = self.embedding_id

        source_job_id = self.source_job_id

        recorded_at: None | str
        recorded_at = self.recorded_at

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "person_id": person_id,
                "person_name": person_name,
                "similarity": similarity,
                "embedding_id": embedding_id,
                "source_job_id": source_job_id,
                "recorded_at": recorded_at,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        person_id = d.pop("person_id")

        def _parse_person_name(data: object) -> None | str:
            if data is None:
                return data
            return cast(None | str, data)

        person_name = _parse_person_name(d.pop("person_name"))

        similarity = d.pop("similarity")

        embedding_id = d.pop("embedding_id")

        source_job_id = d.pop("source_job_id")

        def _parse_recorded_at(data: object) -> None | str:
            if data is None:
                return data
            return cast(None | str, data)

        recorded_at = _parse_recorded_at(d.pop("recorded_at"))

        identify_match = cls(
            person_id=person_id,
            person_name=person_name,
            similarity=similarity,
            embedding_id=embedding_id,
            source_job_id=source_job_id,
            recorded_at=recorded_at,
        )

        identify_match.additional_properties = d
        return identify_match

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
