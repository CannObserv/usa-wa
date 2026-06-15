from __future__ import annotations

import datetime
from collections.abc import Mapping
from typing import Any, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..types import UNSET, Unset

T = TypeVar("T", bound="EmbeddingPatchRequest")


@_attrs_define
class EmbeddingPatchRequest:
    """Mutable metadata fields for PATCH /people/{id}/embeddings/{embedding_id}.

    At least one field must be provided.  The embedding vector, model_id, and
    provenance key fields (source_service, source_job_id, source_segment) are
    identity — not patchable here.

        Attributes:
            activity_ms (int | None | Unset):
            audio_sample_rate_hz (int | None | Unset):
            recorded_at (datetime.datetime | None | Unset):
    """

    activity_ms: int | None | Unset = UNSET
    audio_sample_rate_hz: int | None | Unset = UNSET
    recorded_at: datetime.datetime | None | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        activity_ms: int | None | Unset
        if isinstance(self.activity_ms, Unset):
            activity_ms = UNSET
        else:
            activity_ms = self.activity_ms

        audio_sample_rate_hz: int | None | Unset
        if isinstance(self.audio_sample_rate_hz, Unset):
            audio_sample_rate_hz = UNSET
        else:
            audio_sample_rate_hz = self.audio_sample_rate_hz

        recorded_at: None | str | Unset
        if isinstance(self.recorded_at, Unset):
            recorded_at = UNSET
        elif isinstance(self.recorded_at, datetime.datetime):
            recorded_at = self.recorded_at.isoformat()
        else:
            recorded_at = self.recorded_at

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update({})
        if activity_ms is not UNSET:
            field_dict["activity_ms"] = activity_ms
        if audio_sample_rate_hz is not UNSET:
            field_dict["audio_sample_rate_hz"] = audio_sample_rate_hz
        if recorded_at is not UNSET:
            field_dict["recorded_at"] = recorded_at

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)

        def _parse_activity_ms(data: object) -> int | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(int | None | Unset, data)

        activity_ms = _parse_activity_ms(d.pop("activity_ms", UNSET))

        def _parse_audio_sample_rate_hz(data: object) -> int | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(int | None | Unset, data)

        audio_sample_rate_hz = _parse_audio_sample_rate_hz(d.pop("audio_sample_rate_hz", UNSET))

        def _parse_recorded_at(data: object) -> datetime.datetime | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, str):
                    raise TypeError()
                recorded_at_type_0 = datetime.datetime.fromisoformat(data)

                return recorded_at_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(datetime.datetime | None | Unset, data)

        recorded_at = _parse_recorded_at(d.pop("recorded_at", UNSET))

        embedding_patch_request = cls(
            activity_ms=activity_ms,
            audio_sample_rate_hz=audio_sample_rate_hz,
            recorded_at=recorded_at,
        )

        embedding_patch_request.additional_properties = d
        return embedding_patch_request

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
