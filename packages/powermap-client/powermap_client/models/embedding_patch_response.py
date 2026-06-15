from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field

T = TypeVar("T", bound="EmbeddingPatchResponse")


@_attrs_define
class EmbeddingPatchResponse:
    """Response for a successful PATCH — returns all mutable fields after update.

    Attributes:
        embedding_id (str):
        person_id (str):
        activity_ms (int):
        audio_sample_rate_hz (int):
        recorded_at (None | str):
        created_at (None | str):
    """

    embedding_id: str
    person_id: str
    activity_ms: int
    audio_sample_rate_hz: int
    recorded_at: None | str
    created_at: None | str
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        embedding_id = self.embedding_id

        person_id = self.person_id

        activity_ms = self.activity_ms

        audio_sample_rate_hz = self.audio_sample_rate_hz

        recorded_at: None | str
        recorded_at = self.recorded_at

        created_at: None | str
        created_at = self.created_at

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "embedding_id": embedding_id,
                "person_id": person_id,
                "activity_ms": activity_ms,
                "audio_sample_rate_hz": audio_sample_rate_hz,
                "recorded_at": recorded_at,
                "created_at": created_at,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        embedding_id = d.pop("embedding_id")

        person_id = d.pop("person_id")

        activity_ms = d.pop("activity_ms")

        audio_sample_rate_hz = d.pop("audio_sample_rate_hz")

        def _parse_recorded_at(data: object) -> None | str:
            if data is None:
                return data
            return cast(None | str, data)

        recorded_at = _parse_recorded_at(d.pop("recorded_at"))

        def _parse_created_at(data: object) -> None | str:
            if data is None:
                return data
            return cast(None | str, data)

        created_at = _parse_created_at(d.pop("created_at"))

        embedding_patch_response = cls(
            embedding_id=embedding_id,
            person_id=person_id,
            activity_ms=activity_ms,
            audio_sample_rate_hz=audio_sample_rate_hz,
            recorded_at=recorded_at,
            created_at=created_at,
        )

        embedding_patch_response.additional_properties = d
        return embedding_patch_response

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
