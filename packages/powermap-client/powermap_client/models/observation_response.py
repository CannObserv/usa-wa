from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..models.observation_response_entity_type_type_0 import ObservationResponseEntityTypeType0
from ..types import UNSET, Unset

T = TypeVar("T", bound="ObservationResponse")


@_attrs_define
class ObservationResponse:
    """Response returned by POST /api/v1/observations.

    Attributes:
        disposition (str):
        entity_id (None | str | Unset):
        entity_type (None | ObservationResponseEntityTypeType0 | Unset):
    """

    disposition: str
    entity_id: None | str | Unset = UNSET
    entity_type: None | ObservationResponseEntityTypeType0 | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        disposition = self.disposition

        entity_id: None | str | Unset
        if isinstance(self.entity_id, Unset):
            entity_id = UNSET
        else:
            entity_id = self.entity_id

        entity_type: None | str | Unset
        if isinstance(self.entity_type, Unset):
            entity_type = UNSET
        elif isinstance(self.entity_type, ObservationResponseEntityTypeType0):
            entity_type = self.entity_type.value
        else:
            entity_type = self.entity_type

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "disposition": disposition,
            }
        )
        if entity_id is not UNSET:
            field_dict["entity_id"] = entity_id
        if entity_type is not UNSET:
            field_dict["entity_type"] = entity_type

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        disposition = d.pop("disposition")

        def _parse_entity_id(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        entity_id = _parse_entity_id(d.pop("entity_id", UNSET))

        def _parse_entity_type(data: object) -> None | ObservationResponseEntityTypeType0 | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, str):
                    raise TypeError()
                entity_type_type_0 = ObservationResponseEntityTypeType0(data)

                return entity_type_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(None | ObservationResponseEntityTypeType0 | Unset, data)

        entity_type = _parse_entity_type(d.pop("entity_type", UNSET))

        observation_response = cls(
            disposition=disposition,
            entity_id=entity_id,
            entity_type=entity_type,
        )

        observation_response.additional_properties = d
        return observation_response

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
