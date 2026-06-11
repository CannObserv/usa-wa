from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field

if TYPE_CHECKING:
    from ..models.embedding_source import EmbeddingSource


T = TypeVar("T", bound="EmbeddingWriteRequest")


@_attrs_define
class EmbeddingWriteRequest:
    """Request body for POST /api/v1/people/{id}/embeddings.

    Attributes:
        model_id (str):
        embedding (list[float]):
        activity_ms (int):
        audio_sample_rate_hz (int):
        source (EmbeddingSource): Source provenance for a voice embedding observation.
    """

    model_id: str
    embedding: list[float]
    activity_ms: int
    audio_sample_rate_hz: int
    source: EmbeddingSource
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        model_id = self.model_id

        embedding = self.embedding

        activity_ms = self.activity_ms

        audio_sample_rate_hz = self.audio_sample_rate_hz

        source = self.source.to_dict()

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "model_id": model_id,
                "embedding": embedding,
                "activity_ms": activity_ms,
                "audio_sample_rate_hz": audio_sample_rate_hz,
                "source": source,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.embedding_source import EmbeddingSource

        d = dict(src_dict)
        model_id = d.pop("model_id")

        embedding = cast(list[float], d.pop("embedding"))

        activity_ms = d.pop("activity_ms")

        audio_sample_rate_hz = d.pop("audio_sample_rate_hz")

        source = EmbeddingSource.from_dict(d.pop("source"))

        embedding_write_request = cls(
            model_id=model_id,
            embedding=embedding,
            activity_ms=activity_ms,
            audio_sample_rate_hz=audio_sample_rate_hz,
            source=source,
        )

        embedding_write_request.additional_properties = d
        return embedding_write_request

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
