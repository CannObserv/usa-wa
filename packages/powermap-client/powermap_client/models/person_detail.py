from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.person_identifier import PersonIdentifier
    from ..models.person_name import PersonName


T = TypeVar("T", bound="PersonDetail")


@_attrs_define
class PersonDetail:
    """Full person record including public name variants and identifiers.

    Attributes:
        id (str):
        created_at (str):
        updated_at (str):
        display_name (None | str | Unset):
        archived_at (None | str | Unset):
        names (list[PersonName] | Unset):
        identifiers (list[PersonIdentifier] | Unset):
        voice_embeddings_count (int | Unset):  Default: 0.
    """

    id: str
    created_at: str
    updated_at: str
    display_name: None | str | Unset = UNSET
    archived_at: None | str | Unset = UNSET
    names: list[PersonName] | Unset = UNSET
    identifiers: list[PersonIdentifier] | Unset = UNSET
    voice_embeddings_count: int | Unset = 0
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        id = self.id

        created_at = self.created_at

        updated_at = self.updated_at

        display_name: None | str | Unset
        if isinstance(self.display_name, Unset):
            display_name = UNSET
        else:
            display_name = self.display_name

        archived_at: None | str | Unset
        if isinstance(self.archived_at, Unset):
            archived_at = UNSET
        else:
            archived_at = self.archived_at

        names: list[dict[str, Any]] | Unset = UNSET
        if not isinstance(self.names, Unset):
            names = []
            for names_item_data in self.names:
                names_item = names_item_data.to_dict()
                names.append(names_item)

        identifiers: list[dict[str, Any]] | Unset = UNSET
        if not isinstance(self.identifiers, Unset):
            identifiers = []
            for identifiers_item_data in self.identifiers:
                identifiers_item = identifiers_item_data.to_dict()
                identifiers.append(identifiers_item)

        voice_embeddings_count = self.voice_embeddings_count

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "id": id,
                "created_at": created_at,
                "updated_at": updated_at,
            }
        )
        if display_name is not UNSET:
            field_dict["display_name"] = display_name
        if archived_at is not UNSET:
            field_dict["archived_at"] = archived_at
        if names is not UNSET:
            field_dict["names"] = names
        if identifiers is not UNSET:
            field_dict["identifiers"] = identifiers
        if voice_embeddings_count is not UNSET:
            field_dict["voice_embeddings_count"] = voice_embeddings_count

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.person_identifier import PersonIdentifier
        from ..models.person_name import PersonName

        d = dict(src_dict)
        id = d.pop("id")

        created_at = d.pop("created_at")

        updated_at = d.pop("updated_at")

        def _parse_display_name(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        display_name = _parse_display_name(d.pop("display_name", UNSET))

        def _parse_archived_at(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        archived_at = _parse_archived_at(d.pop("archived_at", UNSET))

        _names = d.pop("names", UNSET)
        names: list[PersonName] | Unset = UNSET
        if _names is not UNSET:
            names = []
            for names_item_data in _names:
                names_item = PersonName.from_dict(names_item_data)

                names.append(names_item)

        _identifiers = d.pop("identifiers", UNSET)
        identifiers: list[PersonIdentifier] | Unset = UNSET
        if _identifiers is not UNSET:
            identifiers = []
            for identifiers_item_data in _identifiers:
                identifiers_item = PersonIdentifier.from_dict(identifiers_item_data)

                identifiers.append(identifiers_item)

        voice_embeddings_count = d.pop("voice_embeddings_count", UNSET)

        person_detail = cls(
            id=id,
            created_at=created_at,
            updated_at=updated_at,
            display_name=display_name,
            archived_at=archived_at,
            names=names,
            identifiers=identifiers,
            voice_embeddings_count=voice_embeddings_count,
        )

        person_detail.additional_properties = d
        return person_detail

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
