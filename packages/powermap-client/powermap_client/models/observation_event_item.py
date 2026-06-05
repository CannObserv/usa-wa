from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..models.observation_event_item_linked_entity_type_type_0 import ObservationEventItemLinkedEntityTypeType0
from ..models.observation_event_item_visibility import ObservationEventItemVisibility
from ..types import UNSET, Unset

T = TypeVar("T", bound="ObservationEventItem")


@_attrs_define
class ObservationEventItem:
    """A lifecycle event claim included in an observation.

    Attributes:
        event_type_id (None | str | Unset):
        event_type_slug (None | str | Unset):
        event_year (int | None | Unset):
        event_month (int | None | Unset):
        event_day (int | None | Unset):
        event_hour (int | None | Unset):
        event_minute (int | None | Unset):
        event_second (int | None | Unset):
        event_place_text (None | str | Unset):
        linked_entity_type (None | ObservationEventItemLinkedEntityTypeType0 | Unset):
        linked_entity_id (None | str | Unset):
        notes (None | str | Unset):
        visibility (ObservationEventItemVisibility | Unset):  Default: ObservationEventItemVisibility.PUBLIC.
    """

    event_type_id: None | str | Unset = UNSET
    event_type_slug: None | str | Unset = UNSET
    event_year: int | None | Unset = UNSET
    event_month: int | None | Unset = UNSET
    event_day: int | None | Unset = UNSET
    event_hour: int | None | Unset = UNSET
    event_minute: int | None | Unset = UNSET
    event_second: int | None | Unset = UNSET
    event_place_text: None | str | Unset = UNSET
    linked_entity_type: None | ObservationEventItemLinkedEntityTypeType0 | Unset = UNSET
    linked_entity_id: None | str | Unset = UNSET
    notes: None | str | Unset = UNSET
    visibility: ObservationEventItemVisibility | Unset = ObservationEventItemVisibility.PUBLIC
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        event_type_id: None | str | Unset
        if isinstance(self.event_type_id, Unset):
            event_type_id = UNSET
        else:
            event_type_id = self.event_type_id

        event_type_slug: None | str | Unset
        if isinstance(self.event_type_slug, Unset):
            event_type_slug = UNSET
        else:
            event_type_slug = self.event_type_slug

        event_year: int | None | Unset
        if isinstance(self.event_year, Unset):
            event_year = UNSET
        else:
            event_year = self.event_year

        event_month: int | None | Unset
        if isinstance(self.event_month, Unset):
            event_month = UNSET
        else:
            event_month = self.event_month

        event_day: int | None | Unset
        if isinstance(self.event_day, Unset):
            event_day = UNSET
        else:
            event_day = self.event_day

        event_hour: int | None | Unset
        if isinstance(self.event_hour, Unset):
            event_hour = UNSET
        else:
            event_hour = self.event_hour

        event_minute: int | None | Unset
        if isinstance(self.event_minute, Unset):
            event_minute = UNSET
        else:
            event_minute = self.event_minute

        event_second: int | None | Unset
        if isinstance(self.event_second, Unset):
            event_second = UNSET
        else:
            event_second = self.event_second

        event_place_text: None | str | Unset
        if isinstance(self.event_place_text, Unset):
            event_place_text = UNSET
        else:
            event_place_text = self.event_place_text

        linked_entity_type: None | str | Unset
        if isinstance(self.linked_entity_type, Unset):
            linked_entity_type = UNSET
        elif isinstance(self.linked_entity_type, ObservationEventItemLinkedEntityTypeType0):
            linked_entity_type = self.linked_entity_type.value
        else:
            linked_entity_type = self.linked_entity_type

        linked_entity_id: None | str | Unset
        if isinstance(self.linked_entity_id, Unset):
            linked_entity_id = UNSET
        else:
            linked_entity_id = self.linked_entity_id

        notes: None | str | Unset
        if isinstance(self.notes, Unset):
            notes = UNSET
        else:
            notes = self.notes

        visibility: str | Unset = UNSET
        if not isinstance(self.visibility, Unset):
            visibility = self.visibility.value

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update({})
        if event_type_id is not UNSET:
            field_dict["event_type_id"] = event_type_id
        if event_type_slug is not UNSET:
            field_dict["event_type_slug"] = event_type_slug
        if event_year is not UNSET:
            field_dict["event_year"] = event_year
        if event_month is not UNSET:
            field_dict["event_month"] = event_month
        if event_day is not UNSET:
            field_dict["event_day"] = event_day
        if event_hour is not UNSET:
            field_dict["event_hour"] = event_hour
        if event_minute is not UNSET:
            field_dict["event_minute"] = event_minute
        if event_second is not UNSET:
            field_dict["event_second"] = event_second
        if event_place_text is not UNSET:
            field_dict["event_place_text"] = event_place_text
        if linked_entity_type is not UNSET:
            field_dict["linked_entity_type"] = linked_entity_type
        if linked_entity_id is not UNSET:
            field_dict["linked_entity_id"] = linked_entity_id
        if notes is not UNSET:
            field_dict["notes"] = notes
        if visibility is not UNSET:
            field_dict["visibility"] = visibility

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)

        def _parse_event_type_id(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        event_type_id = _parse_event_type_id(d.pop("event_type_id", UNSET))

        def _parse_event_type_slug(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        event_type_slug = _parse_event_type_slug(d.pop("event_type_slug", UNSET))

        def _parse_event_year(data: object) -> int | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(int | None | Unset, data)

        event_year = _parse_event_year(d.pop("event_year", UNSET))

        def _parse_event_month(data: object) -> int | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(int | None | Unset, data)

        event_month = _parse_event_month(d.pop("event_month", UNSET))

        def _parse_event_day(data: object) -> int | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(int | None | Unset, data)

        event_day = _parse_event_day(d.pop("event_day", UNSET))

        def _parse_event_hour(data: object) -> int | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(int | None | Unset, data)

        event_hour = _parse_event_hour(d.pop("event_hour", UNSET))

        def _parse_event_minute(data: object) -> int | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(int | None | Unset, data)

        event_minute = _parse_event_minute(d.pop("event_minute", UNSET))

        def _parse_event_second(data: object) -> int | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(int | None | Unset, data)

        event_second = _parse_event_second(d.pop("event_second", UNSET))

        def _parse_event_place_text(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        event_place_text = _parse_event_place_text(d.pop("event_place_text", UNSET))

        def _parse_linked_entity_type(data: object) -> None | ObservationEventItemLinkedEntityTypeType0 | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, str):
                    raise TypeError()
                linked_entity_type_type_0 = ObservationEventItemLinkedEntityTypeType0(data)

                return linked_entity_type_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(None | ObservationEventItemLinkedEntityTypeType0 | Unset, data)

        linked_entity_type = _parse_linked_entity_type(d.pop("linked_entity_type", UNSET))

        def _parse_linked_entity_id(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        linked_entity_id = _parse_linked_entity_id(d.pop("linked_entity_id", UNSET))

        def _parse_notes(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        notes = _parse_notes(d.pop("notes", UNSET))

        _visibility = d.pop("visibility", UNSET)
        visibility: ObservationEventItemVisibility | Unset
        if isinstance(_visibility, Unset):
            visibility = UNSET
        else:
            visibility = ObservationEventItemVisibility(_visibility)

        observation_event_item = cls(
            event_type_id=event_type_id,
            event_type_slug=event_type_slug,
            event_year=event_year,
            event_month=event_month,
            event_day=event_day,
            event_hour=event_hour,
            event_minute=event_minute,
            event_second=event_second,
            event_place_text=event_place_text,
            linked_entity_type=linked_entity_type,
            linked_entity_id=linked_entity_id,
            notes=notes,
            visibility=visibility,
        )

        observation_event_item.additional_properties = d
        return observation_event_item

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
