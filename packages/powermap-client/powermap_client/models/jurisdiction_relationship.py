from __future__ import annotations

import datetime
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.jurisdiction_relationship_type import JurisdictionRelationshipType


T = TypeVar("T", bound="JurisdictionRelationship")


@_attrs_define
class JurisdictionRelationship:
    """A typed edge in the jurisdiction graph.

    Attributes:
        id (str):
        from_id (str):
        to_id (str):
        rel_type (JurisdictionRelationshipType): A jurisdiction relationship type from the lookup table.
        recorded_at (None | str):
        created_at (None | str):
        valid_from (datetime.date | None | Unset):
        valid_until (datetime.date | None | Unset):
        superseded_at (None | str | Unset):
    """

    id: str
    from_id: str
    to_id: str
    rel_type: JurisdictionRelationshipType
    recorded_at: None | str
    created_at: None | str
    valid_from: datetime.date | None | Unset = UNSET
    valid_until: datetime.date | None | Unset = UNSET
    superseded_at: None | str | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        id = self.id

        from_id = self.from_id

        to_id = self.to_id

        rel_type = self.rel_type.to_dict()

        recorded_at: None | str
        recorded_at = self.recorded_at

        created_at: None | str
        created_at = self.created_at

        valid_from: None | str | Unset
        if isinstance(self.valid_from, Unset):
            valid_from = UNSET
        elif isinstance(self.valid_from, datetime.date):
            valid_from = self.valid_from.isoformat()
        else:
            valid_from = self.valid_from

        valid_until: None | str | Unset
        if isinstance(self.valid_until, Unset):
            valid_until = UNSET
        elif isinstance(self.valid_until, datetime.date):
            valid_until = self.valid_until.isoformat()
        else:
            valid_until = self.valid_until

        superseded_at: None | str | Unset
        if isinstance(self.superseded_at, Unset):
            superseded_at = UNSET
        else:
            superseded_at = self.superseded_at

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "id": id,
                "from_id": from_id,
                "to_id": to_id,
                "rel_type": rel_type,
                "recorded_at": recorded_at,
                "created_at": created_at,
            }
        )
        if valid_from is not UNSET:
            field_dict["valid_from"] = valid_from
        if valid_until is not UNSET:
            field_dict["valid_until"] = valid_until
        if superseded_at is not UNSET:
            field_dict["superseded_at"] = superseded_at

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.jurisdiction_relationship_type import JurisdictionRelationshipType

        d = dict(src_dict)
        id = d.pop("id")

        from_id = d.pop("from_id")

        to_id = d.pop("to_id")

        rel_type = JurisdictionRelationshipType.from_dict(d.pop("rel_type"))

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

        def _parse_valid_from(data: object) -> datetime.date | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, str):
                    raise TypeError()
                valid_from_type_0 = datetime.date.fromisoformat(data)

                return valid_from_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(datetime.date | None | Unset, data)

        valid_from = _parse_valid_from(d.pop("valid_from", UNSET))

        def _parse_valid_until(data: object) -> datetime.date | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, str):
                    raise TypeError()
                valid_until_type_0 = datetime.date.fromisoformat(data)

                return valid_until_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(datetime.date | None | Unset, data)

        valid_until = _parse_valid_until(d.pop("valid_until", UNSET))

        def _parse_superseded_at(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        superseded_at = _parse_superseded_at(d.pop("superseded_at", UNSET))

        jurisdiction_relationship = cls(
            id=id,
            from_id=from_id,
            to_id=to_id,
            rel_type=rel_type,
            recorded_at=recorded_at,
            created_at=created_at,
            valid_from=valid_from,
            valid_until=valid_until,
            superseded_at=superseded_at,
        )

        jurisdiction_relationship.additional_properties = d
        return jurisdiction_relationship

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
