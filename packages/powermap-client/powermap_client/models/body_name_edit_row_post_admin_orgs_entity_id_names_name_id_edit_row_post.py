from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..models.body_name_edit_row_post_admin_orgs_entity_id_names_name_id_edit_row_post_visibility_type_0 import (
    BodyNameEditRowPostAdminOrgsEntityIdNamesNameIdEditRowPostVisibilityType0,
)
from ..types import UNSET, Unset

T = TypeVar("T", bound="BodyNameEditRowPostAdminOrgsEntityIdNamesNameIdEditRowPost")


@_attrs_define
class BodyNameEditRowPostAdminOrgsEntityIdNamesNameIdEditRowPost:
    """
    Attributes:
        name (str):
        name_type (str | Unset):  Default: 'legal'.
        is_canonical (str | Unset):  Default: ''.
        visibility (BodyNameEditRowPostAdminOrgsEntityIdNamesNameIdEditRowPostVisibilityType0 | None | Unset):
        locale (None | str | Unset):
        script (None | str | Unset):
        sort_as (None | str | Unset):
        reading_of_id (None | str | Unset):
        effective_start (None | str | Unset):
        effective_end (None | str | Unset):
        given_names (list[str] | Unset):
        family_names (list[str] | Unset):
        additional_names (list[str] | Unset):
        honorific_prefix (None | str | Unset):
        honorific_suffix (None | str | Unset):
        primary_identifier (None | str | Unset):
    """

    name: str
    name_type: str | Unset = "legal"
    is_canonical: str | Unset = ""
    visibility: BodyNameEditRowPostAdminOrgsEntityIdNamesNameIdEditRowPostVisibilityType0 | None | Unset = UNSET
    locale: None | str | Unset = UNSET
    script: None | str | Unset = UNSET
    sort_as: None | str | Unset = UNSET
    reading_of_id: None | str | Unset = UNSET
    effective_start: None | str | Unset = UNSET
    effective_end: None | str | Unset = UNSET
    given_names: list[str] | Unset = UNSET
    family_names: list[str] | Unset = UNSET
    additional_names: list[str] | Unset = UNSET
    honorific_prefix: None | str | Unset = UNSET
    honorific_suffix: None | str | Unset = UNSET
    primary_identifier: None | str | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        name = self.name

        name_type = self.name_type

        is_canonical = self.is_canonical

        visibility: None | str | Unset
        if isinstance(self.visibility, Unset):
            visibility = UNSET
        elif isinstance(self.visibility, BodyNameEditRowPostAdminOrgsEntityIdNamesNameIdEditRowPostVisibilityType0):
            visibility = self.visibility.value
        else:
            visibility = self.visibility

        locale: None | str | Unset
        if isinstance(self.locale, Unset):
            locale = UNSET
        else:
            locale = self.locale

        script: None | str | Unset
        if isinstance(self.script, Unset):
            script = UNSET
        else:
            script = self.script

        sort_as: None | str | Unset
        if isinstance(self.sort_as, Unset):
            sort_as = UNSET
        else:
            sort_as = self.sort_as

        reading_of_id: None | str | Unset
        if isinstance(self.reading_of_id, Unset):
            reading_of_id = UNSET
        else:
            reading_of_id = self.reading_of_id

        effective_start: None | str | Unset
        if isinstance(self.effective_start, Unset):
            effective_start = UNSET
        else:
            effective_start = self.effective_start

        effective_end: None | str | Unset
        if isinstance(self.effective_end, Unset):
            effective_end = UNSET
        else:
            effective_end = self.effective_end

        given_names: list[str] | Unset = UNSET
        if not isinstance(self.given_names, Unset):
            given_names = self.given_names

        family_names: list[str] | Unset = UNSET
        if not isinstance(self.family_names, Unset):
            family_names = self.family_names

        additional_names: list[str] | Unset = UNSET
        if not isinstance(self.additional_names, Unset):
            additional_names = self.additional_names

        honorific_prefix: None | str | Unset
        if isinstance(self.honorific_prefix, Unset):
            honorific_prefix = UNSET
        else:
            honorific_prefix = self.honorific_prefix

        honorific_suffix: None | str | Unset
        if isinstance(self.honorific_suffix, Unset):
            honorific_suffix = UNSET
        else:
            honorific_suffix = self.honorific_suffix

        primary_identifier: None | str | Unset
        if isinstance(self.primary_identifier, Unset):
            primary_identifier = UNSET
        else:
            primary_identifier = self.primary_identifier

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "name": name,
            }
        )
        if name_type is not UNSET:
            field_dict["name_type"] = name_type
        if is_canonical is not UNSET:
            field_dict["is_canonical"] = is_canonical
        if visibility is not UNSET:
            field_dict["visibility"] = visibility
        if locale is not UNSET:
            field_dict["locale"] = locale
        if script is not UNSET:
            field_dict["script"] = script
        if sort_as is not UNSET:
            field_dict["sort_as"] = sort_as
        if reading_of_id is not UNSET:
            field_dict["reading_of_id"] = reading_of_id
        if effective_start is not UNSET:
            field_dict["effective_start"] = effective_start
        if effective_end is not UNSET:
            field_dict["effective_end"] = effective_end
        if given_names is not UNSET:
            field_dict["given_names"] = given_names
        if family_names is not UNSET:
            field_dict["family_names"] = family_names
        if additional_names is not UNSET:
            field_dict["additional_names"] = additional_names
        if honorific_prefix is not UNSET:
            field_dict["honorific_prefix"] = honorific_prefix
        if honorific_suffix is not UNSET:
            field_dict["honorific_suffix"] = honorific_suffix
        if primary_identifier is not UNSET:
            field_dict["primary_identifier"] = primary_identifier

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        name = d.pop("name")

        name_type = d.pop("name_type", UNSET)

        is_canonical = d.pop("is_canonical", UNSET)

        def _parse_visibility(
            data: object,
        ) -> BodyNameEditRowPostAdminOrgsEntityIdNamesNameIdEditRowPostVisibilityType0 | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, str):
                    raise TypeError()
                visibility_type_0 = BodyNameEditRowPostAdminOrgsEntityIdNamesNameIdEditRowPostVisibilityType0(data)

                return visibility_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(BodyNameEditRowPostAdminOrgsEntityIdNamesNameIdEditRowPostVisibilityType0 | None | Unset, data)

        visibility = _parse_visibility(d.pop("visibility", UNSET))

        def _parse_locale(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        locale = _parse_locale(d.pop("locale", UNSET))

        def _parse_script(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        script = _parse_script(d.pop("script", UNSET))

        def _parse_sort_as(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        sort_as = _parse_sort_as(d.pop("sort_as", UNSET))

        def _parse_reading_of_id(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        reading_of_id = _parse_reading_of_id(d.pop("reading_of_id", UNSET))

        def _parse_effective_start(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        effective_start = _parse_effective_start(d.pop("effective_start", UNSET))

        def _parse_effective_end(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        effective_end = _parse_effective_end(d.pop("effective_end", UNSET))

        given_names = cast(list[str], d.pop("given_names", UNSET))

        family_names = cast(list[str], d.pop("family_names", UNSET))

        additional_names = cast(list[str], d.pop("additional_names", UNSET))

        def _parse_honorific_prefix(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        honorific_prefix = _parse_honorific_prefix(d.pop("honorific_prefix", UNSET))

        def _parse_honorific_suffix(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        honorific_suffix = _parse_honorific_suffix(d.pop("honorific_suffix", UNSET))

        def _parse_primary_identifier(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        primary_identifier = _parse_primary_identifier(d.pop("primary_identifier", UNSET))

        body_name_edit_row_post_admin_orgs_entity_id_names_name_id_edit_row_post = cls(
            name=name,
            name_type=name_type,
            is_canonical=is_canonical,
            visibility=visibility,
            locale=locale,
            script=script,
            sort_as=sort_as,
            reading_of_id=reading_of_id,
            effective_start=effective_start,
            effective_end=effective_end,
            given_names=given_names,
            family_names=family_names,
            additional_names=additional_names,
            honorific_prefix=honorific_prefix,
            honorific_suffix=honorific_suffix,
            primary_identifier=primary_identifier,
        )

        body_name_edit_row_post_admin_orgs_entity_id_names_name_id_edit_row_post.additional_properties = d
        return body_name_edit_row_post_admin_orgs_entity_id_names_name_id_edit_row_post

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
