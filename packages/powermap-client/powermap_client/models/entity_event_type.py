from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..models.entity_event_type_applies_to import EntityEventTypeAppliesTo

T = TypeVar("T", bound="EntityEventType")


@_attrs_define
class EntityEventType:
    """An entity event type used to classify life/organisational events.

    Attributes:
        id (str):
        slug (str):
        display_name (str):
        applies_to (EntityEventTypeAppliesTo):
        requires_year (bool):
        requires_linked_entity (bool):
    """

    id: str
    slug: str
    display_name: str
    applies_to: EntityEventTypeAppliesTo
    requires_year: bool
    requires_linked_entity: bool
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        id = self.id

        slug = self.slug

        display_name = self.display_name

        applies_to = self.applies_to.value

        requires_year = self.requires_year

        requires_linked_entity = self.requires_linked_entity

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "id": id,
                "slug": slug,
                "display_name": display_name,
                "applies_to": applies_to,
                "requires_year": requires_year,
                "requires_linked_entity": requires_linked_entity,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        id = d.pop("id")

        slug = d.pop("slug")

        display_name = d.pop("display_name")

        applies_to = EntityEventTypeAppliesTo(d.pop("applies_to"))

        requires_year = d.pop("requires_year")

        requires_linked_entity = d.pop("requires_linked_entity")

        entity_event_type = cls(
            id=id,
            slug=slug,
            display_name=display_name,
            applies_to=applies_to,
            requires_year=requires_year,
            requires_linked_entity=requires_linked_entity,
        )

        entity_event_type.additional_properties = d
        return entity_event_type

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
