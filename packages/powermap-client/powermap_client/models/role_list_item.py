from __future__ import annotations

import datetime
from collections.abc import Mapping
from typing import Any, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..types import UNSET, Unset

T = TypeVar("T", bound="RoleListItem")


@_attrs_define
class RoleListItem:
    """Single item in a role list response.

    Attributes:
        id (str):
        organization_id (str):
        title (str):
        created_at (None | str):
        updated_at (None | str):
        notes (None | str | Unset):
        established_on (datetime.date | None | Unset):
        abolished_on (datetime.date | None | Unset):
        role_type_id (None | str | Unset):
        role_type_slug (None | str | Unset):
        jurisdiction_id (None | str | Unset):
        qualifier (None | str | Unset):
        archived_at (None | str | Unset):
    """

    id: str
    organization_id: str
    title: str
    created_at: None | str
    updated_at: None | str
    notes: None | str | Unset = UNSET
    established_on: datetime.date | None | Unset = UNSET
    abolished_on: datetime.date | None | Unset = UNSET
    role_type_id: None | str | Unset = UNSET
    role_type_slug: None | str | Unset = UNSET
    jurisdiction_id: None | str | Unset = UNSET
    qualifier: None | str | Unset = UNSET
    archived_at: None | str | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        id = self.id

        organization_id = self.organization_id

        title = self.title

        created_at: None | str
        created_at = self.created_at

        updated_at: None | str
        updated_at = self.updated_at

        notes: None | str | Unset
        if isinstance(self.notes, Unset):
            notes = UNSET
        else:
            notes = self.notes

        established_on: None | str | Unset
        if isinstance(self.established_on, Unset):
            established_on = UNSET
        elif isinstance(self.established_on, datetime.date):
            established_on = self.established_on.isoformat()
        else:
            established_on = self.established_on

        abolished_on: None | str | Unset
        if isinstance(self.abolished_on, Unset):
            abolished_on = UNSET
        elif isinstance(self.abolished_on, datetime.date):
            abolished_on = self.abolished_on.isoformat()
        else:
            abolished_on = self.abolished_on

        role_type_id: None | str | Unset
        if isinstance(self.role_type_id, Unset):
            role_type_id = UNSET
        else:
            role_type_id = self.role_type_id

        role_type_slug: None | str | Unset
        if isinstance(self.role_type_slug, Unset):
            role_type_slug = UNSET
        else:
            role_type_slug = self.role_type_slug

        jurisdiction_id: None | str | Unset
        if isinstance(self.jurisdiction_id, Unset):
            jurisdiction_id = UNSET
        else:
            jurisdiction_id = self.jurisdiction_id

        qualifier: None | str | Unset
        if isinstance(self.qualifier, Unset):
            qualifier = UNSET
        else:
            qualifier = self.qualifier

        archived_at: None | str | Unset
        if isinstance(self.archived_at, Unset):
            archived_at = UNSET
        else:
            archived_at = self.archived_at

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "id": id,
                "organization_id": organization_id,
                "title": title,
                "created_at": created_at,
                "updated_at": updated_at,
            }
        )
        if notes is not UNSET:
            field_dict["notes"] = notes
        if established_on is not UNSET:
            field_dict["established_on"] = established_on
        if abolished_on is not UNSET:
            field_dict["abolished_on"] = abolished_on
        if role_type_id is not UNSET:
            field_dict["role_type_id"] = role_type_id
        if role_type_slug is not UNSET:
            field_dict["role_type_slug"] = role_type_slug
        if jurisdiction_id is not UNSET:
            field_dict["jurisdiction_id"] = jurisdiction_id
        if qualifier is not UNSET:
            field_dict["qualifier"] = qualifier
        if archived_at is not UNSET:
            field_dict["archived_at"] = archived_at

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        id = d.pop("id")

        organization_id = d.pop("organization_id")

        title = d.pop("title")

        def _parse_created_at(data: object) -> None | str:
            if data is None:
                return data
            return cast(None | str, data)

        created_at = _parse_created_at(d.pop("created_at"))

        def _parse_updated_at(data: object) -> None | str:
            if data is None:
                return data
            return cast(None | str, data)

        updated_at = _parse_updated_at(d.pop("updated_at"))

        def _parse_notes(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        notes = _parse_notes(d.pop("notes", UNSET))

        def _parse_established_on(data: object) -> datetime.date | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, str):
                    raise TypeError()
                established_on_type_0 = datetime.date.fromisoformat(data)

                return established_on_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(datetime.date | None | Unset, data)

        established_on = _parse_established_on(d.pop("established_on", UNSET))

        def _parse_abolished_on(data: object) -> datetime.date | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, str):
                    raise TypeError()
                abolished_on_type_0 = datetime.date.fromisoformat(data)

                return abolished_on_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(datetime.date | None | Unset, data)

        abolished_on = _parse_abolished_on(d.pop("abolished_on", UNSET))

        def _parse_role_type_id(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        role_type_id = _parse_role_type_id(d.pop("role_type_id", UNSET))

        def _parse_role_type_slug(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        role_type_slug = _parse_role_type_slug(d.pop("role_type_slug", UNSET))

        def _parse_jurisdiction_id(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        jurisdiction_id = _parse_jurisdiction_id(d.pop("jurisdiction_id", UNSET))

        def _parse_qualifier(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        qualifier = _parse_qualifier(d.pop("qualifier", UNSET))

        def _parse_archived_at(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        archived_at = _parse_archived_at(d.pop("archived_at", UNSET))

        role_list_item = cls(
            id=id,
            organization_id=organization_id,
            title=title,
            created_at=created_at,
            updated_at=updated_at,
            notes=notes,
            established_on=established_on,
            abolished_on=abolished_on,
            role_type_id=role_type_id,
            role_type_slug=role_type_slug,
            jurisdiction_id=jurisdiction_id,
            qualifier=qualifier,
            archived_at=archived_at,
        )

        role_list_item.additional_properties = d
        return role_list_item

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
