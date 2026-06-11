from __future__ import annotations

import datetime
from collections.abc import Mapping
from typing import Any, TypeVar

from attrs import define as _attrs_define
from attrs import field as _attrs_field

T = TypeVar("T", bound="EmbeddingSource")


@_attrs_define
class EmbeddingSource:
    """Source provenance for a voice embedding observation.

    Attributes:
        service (str):
        job_id (str):
        segment (int):
        recorded_at (datetime.datetime):
    """

    service: str
    job_id: str
    segment: int
    recorded_at: datetime.datetime
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        service = self.service

        job_id = self.job_id

        segment = self.segment

        recorded_at = self.recorded_at.isoformat()

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "service": service,
                "job_id": job_id,
                "segment": segment,
                "recorded_at": recorded_at,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        service = d.pop("service")

        job_id = d.pop("job_id")

        segment = d.pop("segment")

        recorded_at = datetime.datetime.fromisoformat(d.pop("recorded_at"))

        embedding_source = cls(
            service=service,
            job_id=job_id,
            segment=segment,
            recorded_at=recorded_at,
        )

        embedding_source.additional_properties = d
        return embedding_source

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
