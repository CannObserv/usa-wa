from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..types import UNSET, Unset

T = TypeVar("T", bound="ObservationLink")


@_attrs_define
class ObservationLink:
    """A web URL claim included in an observation.

    Attributes:
        url (str):
        link_type_id (None | str | Unset):
        link_type_slug (None | str | Unset):
    """

    url: str
    link_type_id: None | str | Unset = UNSET
    link_type_slug: None | str | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        url = self.url

        link_type_id: None | str | Unset
        if isinstance(self.link_type_id, Unset):
            link_type_id = UNSET
        else:
            link_type_id = self.link_type_id

        link_type_slug: None | str | Unset
        if isinstance(self.link_type_slug, Unset):
            link_type_slug = UNSET
        else:
            link_type_slug = self.link_type_slug

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "url": url,
            }
        )
        if link_type_id is not UNSET:
            field_dict["link_type_id"] = link_type_id
        if link_type_slug is not UNSET:
            field_dict["link_type_slug"] = link_type_slug

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        url = d.pop("url")

        def _parse_link_type_id(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        link_type_id = _parse_link_type_id(d.pop("link_type_id", UNSET))

        def _parse_link_type_slug(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        link_type_slug = _parse_link_type_slug(d.pop("link_type_slug", UNSET))

        observation_link = cls(
            url=url,
            link_type_id=link_type_id,
            link_type_slug=link_type_slug,
        )

        observation_link.additional_properties = d
        return observation_link

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
