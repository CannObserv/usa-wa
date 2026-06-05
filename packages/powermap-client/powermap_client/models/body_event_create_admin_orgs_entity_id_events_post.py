from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..types import UNSET, Unset

T = TypeVar("T", bound="BodyEventCreateAdminOrgsEntityIdEventsPost")


@_attrs_define
class BodyEventCreateAdminOrgsEntityIdEventsPost:
    """
    Attributes:
        event_type_id (str):
        event_year (str | Unset):  Default: ''.
        event_month (str | Unset):  Default: ''.
        event_day (str | Unset):  Default: ''.
        event_hour (str | Unset):  Default: ''.
        event_minute (str | Unset):  Default: ''.
        event_second (str | Unset):  Default: ''.
        event_place_text (str | Unset):  Default: ''.
        linked_entity_type (str | Unset):  Default: ''.
        linked_entity_id (str | Unset):  Default: ''.
        notes (str | Unset):  Default: ''.
        visibility (str | Unset):  Default: 'public'.
    """

    event_type_id: str
    event_year: str | Unset = ""
    event_month: str | Unset = ""
    event_day: str | Unset = ""
    event_hour: str | Unset = ""
    event_minute: str | Unset = ""
    event_second: str | Unset = ""
    event_place_text: str | Unset = ""
    linked_entity_type: str | Unset = ""
    linked_entity_id: str | Unset = ""
    notes: str | Unset = ""
    visibility: str | Unset = "public"
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        event_type_id = self.event_type_id

        event_year = self.event_year

        event_month = self.event_month

        event_day = self.event_day

        event_hour = self.event_hour

        event_minute = self.event_minute

        event_second = self.event_second

        event_place_text = self.event_place_text

        linked_entity_type = self.linked_entity_type

        linked_entity_id = self.linked_entity_id

        notes = self.notes

        visibility = self.visibility

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "event_type_id": event_type_id,
            }
        )
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
        event_type_id = d.pop("event_type_id")

        event_year = d.pop("event_year", UNSET)

        event_month = d.pop("event_month", UNSET)

        event_day = d.pop("event_day", UNSET)

        event_hour = d.pop("event_hour", UNSET)

        event_minute = d.pop("event_minute", UNSET)

        event_second = d.pop("event_second", UNSET)

        event_place_text = d.pop("event_place_text", UNSET)

        linked_entity_type = d.pop("linked_entity_type", UNSET)

        linked_entity_id = d.pop("linked_entity_id", UNSET)

        notes = d.pop("notes", UNSET)

        visibility = d.pop("visibility", UNSET)

        body_event_create_admin_orgs_entity_id_events_post = cls(
            event_type_id=event_type_id,
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

        body_event_create_admin_orgs_entity_id_events_post.additional_properties = d
        return body_event_create_admin_orgs_entity_id_events_post

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
