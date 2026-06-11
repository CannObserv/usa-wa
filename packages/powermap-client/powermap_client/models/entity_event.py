from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..models.entity_event_linked_entity_type_type_0 import EntityEventLinkedEntityTypeType0
from ..models.entity_event_visibility import EntityEventVisibility
from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.event_place_address import EventPlaceAddress
    from ..models.event_type_inline import EventTypeInline
    from ..models.partial_date import PartialDate


T = TypeVar("T", bound="EntityEvent")


@_attrs_define
class EntityEvent:
    """Single event item in a list response.

    Attributes:
        id (str):
        event_type (EventTypeInline): Event type embedded in event list items.
        date (PartialDate): Partial date/time with explicit precision.
        visibility (EntityEventVisibility):
        created_at (None | str):
        event_place_text (None | str | Unset):
        event_place_address (EventPlaceAddress | None | Unset):
        linked_entity_type (EntityEventLinkedEntityTypeType0 | None | Unset):
        linked_entity_id (None | str | Unset):
        notes (None | str | Unset):
        verified_at (None | str | Unset):
    """

    id: str
    event_type: EventTypeInline
    date: PartialDate
    visibility: EntityEventVisibility
    created_at: None | str
    event_place_text: None | str | Unset = UNSET
    event_place_address: EventPlaceAddress | None | Unset = UNSET
    linked_entity_type: EntityEventLinkedEntityTypeType0 | None | Unset = UNSET
    linked_entity_id: None | str | Unset = UNSET
    notes: None | str | Unset = UNSET
    verified_at: None | str | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        from ..models.event_place_address import EventPlaceAddress

        id = self.id

        event_type = self.event_type.to_dict()

        date = self.date.to_dict()

        visibility = self.visibility.value

        created_at: None | str
        created_at = self.created_at

        event_place_text: None | str | Unset
        if isinstance(self.event_place_text, Unset):
            event_place_text = UNSET
        else:
            event_place_text = self.event_place_text

        event_place_address: dict[str, Any] | None | Unset
        if isinstance(self.event_place_address, Unset):
            event_place_address = UNSET
        elif isinstance(self.event_place_address, EventPlaceAddress):
            event_place_address = self.event_place_address.to_dict()
        else:
            event_place_address = self.event_place_address

        linked_entity_type: None | str | Unset
        if isinstance(self.linked_entity_type, Unset):
            linked_entity_type = UNSET
        elif isinstance(self.linked_entity_type, EntityEventLinkedEntityTypeType0):
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

        verified_at: None | str | Unset
        if isinstance(self.verified_at, Unset):
            verified_at = UNSET
        else:
            verified_at = self.verified_at

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "id": id,
                "event_type": event_type,
                "date": date,
                "visibility": visibility,
                "created_at": created_at,
            }
        )
        if event_place_text is not UNSET:
            field_dict["event_place_text"] = event_place_text
        if event_place_address is not UNSET:
            field_dict["event_place_address"] = event_place_address
        if linked_entity_type is not UNSET:
            field_dict["linked_entity_type"] = linked_entity_type
        if linked_entity_id is not UNSET:
            field_dict["linked_entity_id"] = linked_entity_id
        if notes is not UNSET:
            field_dict["notes"] = notes
        if verified_at is not UNSET:
            field_dict["verified_at"] = verified_at

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.event_place_address import EventPlaceAddress
        from ..models.event_type_inline import EventTypeInline
        from ..models.partial_date import PartialDate

        d = dict(src_dict)
        id = d.pop("id")

        event_type = EventTypeInline.from_dict(d.pop("event_type"))

        date = PartialDate.from_dict(d.pop("date"))

        visibility = EntityEventVisibility(d.pop("visibility"))

        def _parse_created_at(data: object) -> None | str:
            if data is None:
                return data
            return cast(None | str, data)

        created_at = _parse_created_at(d.pop("created_at"))

        def _parse_event_place_text(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        event_place_text = _parse_event_place_text(d.pop("event_place_text", UNSET))

        def _parse_event_place_address(data: object) -> EventPlaceAddress | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, dict):
                    raise TypeError()
                event_place_address_type_0 = EventPlaceAddress.from_dict(data)

                return event_place_address_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(EventPlaceAddress | None | Unset, data)

        event_place_address = _parse_event_place_address(d.pop("event_place_address", UNSET))

        def _parse_linked_entity_type(data: object) -> EntityEventLinkedEntityTypeType0 | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, str):
                    raise TypeError()
                linked_entity_type_type_0 = EntityEventLinkedEntityTypeType0(data)

                return linked_entity_type_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(EntityEventLinkedEntityTypeType0 | None | Unset, data)

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

        def _parse_verified_at(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        verified_at = _parse_verified_at(d.pop("verified_at", UNSET))

        entity_event = cls(
            id=id,
            event_type=event_type,
            date=date,
            visibility=visibility,
            created_at=created_at,
            event_place_text=event_place_text,
            event_place_address=event_place_address,
            linked_entity_type=linked_entity_type,
            linked_entity_id=linked_entity_id,
            notes=notes,
            verified_at=verified_at,
        )

        entity_event.additional_properties = d
        return entity_event

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
