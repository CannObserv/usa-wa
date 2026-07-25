from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..models.observation_response_entity_type_type_0 import ObservationResponseEntityTypeType0
from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.event_observation_result import EventObservationResult


T = TypeVar("T", bound="ObservationResponse")


@_attrs_define
class ObservationResponse:
    """Response returned by POST /api/v1/observations.

    Attributes:
        disposition (str):
        entity_id (None | str | Unset):
        entity_type (None | ObservationResponseEntityTypeType0 | Unset):
        reason (None | str | Unset):
        unapplied (list[str] | None | Unset):
        events (list[EventObservationResult] | None | Unset):
    """

    disposition: str
    entity_id: None | str | Unset = UNSET
    entity_type: None | ObservationResponseEntityTypeType0 | Unset = UNSET
    reason: None | str | Unset = UNSET
    unapplied: list[str] | None | Unset = UNSET
    events: list[EventObservationResult] | None | Unset = UNSET
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

        reason: None | str | Unset
        if isinstance(self.reason, Unset):
            reason = UNSET
        else:
            reason = self.reason

        unapplied: list[str] | None | Unset
        if isinstance(self.unapplied, Unset):
            unapplied = UNSET
        elif isinstance(self.unapplied, list):
            unapplied = self.unapplied

        else:
            unapplied = self.unapplied

        events: list[dict[str, Any]] | None | Unset
        if isinstance(self.events, Unset):
            events = UNSET
        elif isinstance(self.events, list):
            events = []
            for events_type_0_item_data in self.events:
                events_type_0_item = events_type_0_item_data.to_dict()
                events.append(events_type_0_item)

        else:
            events = self.events

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
        if reason is not UNSET:
            field_dict["reason"] = reason
        if unapplied is not UNSET:
            field_dict["unapplied"] = unapplied
        if events is not UNSET:
            field_dict["events"] = events

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.event_observation_result import EventObservationResult

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

        def _parse_reason(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        reason = _parse_reason(d.pop("reason", UNSET))

        def _parse_unapplied(data: object) -> list[str] | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, list):
                    raise TypeError()
                unapplied_type_0 = cast(list[str], data)

                return unapplied_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(list[str] | None | Unset, data)

        unapplied = _parse_unapplied(d.pop("unapplied", UNSET))

        def _parse_events(data: object) -> list[EventObservationResult] | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, list):
                    raise TypeError()
                events_type_0 = []
                _events_type_0 = data
                for events_type_0_item_data in _events_type_0:
                    events_type_0_item = EventObservationResult.from_dict(events_type_0_item_data)

                    events_type_0.append(events_type_0_item)

                return events_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(list[EventObservationResult] | None | Unset, data)

        events = _parse_events(d.pop("events", UNSET))

        observation_response = cls(
            disposition=disposition,
            entity_id=entity_id,
            entity_type=entity_type,
            reason=reason,
            unapplied=unapplied,
            events=events,
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
