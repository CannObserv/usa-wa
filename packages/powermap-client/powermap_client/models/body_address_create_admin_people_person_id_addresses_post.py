from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..types import UNSET, Unset

T = TypeVar("T", bound="BodyAddressCreateAdminPeoplePersonIdAddressesPost")


@_attrs_define
class BodyAddressCreateAdminPeoplePersonIdAddressesPost:
    """
    Attributes:
        address_line_1 (str | Unset):  Default: ''.
        address_line_2 (str | Unset):  Default: ''.
        city (str | Unset):  Default: ''.
        region (str | Unset):  Default: ''.
        postal_code (str | Unset):  Default: ''.
        address_type (str | Unset):  Default: 'mailing'.
        display_name (str | Unset):  Default: ''.
        valid_from (str | Unset):  Default: ''.
        valid_until (str | Unset):  Default: ''.
        mode (str | Unset):  Default: 'confirm'.
        standardized (str | Unset):  Default: ''.
        latitude (str | Unset):  Default: ''.
        longitude (str | Unset):  Default: ''.
        components (str | Unset):  Default: ''.
        country (str | Unset):  Default: 'US'.
    """

    address_line_1: str | Unset = ""
    address_line_2: str | Unset = ""
    city: str | Unset = ""
    region: str | Unset = ""
    postal_code: str | Unset = ""
    address_type: str | Unset = "mailing"
    display_name: str | Unset = ""
    valid_from: str | Unset = ""
    valid_until: str | Unset = ""
    mode: str | Unset = "confirm"
    standardized: str | Unset = ""
    latitude: str | Unset = ""
    longitude: str | Unset = ""
    components: str | Unset = ""
    country: str | Unset = "US"
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        address_line_1 = self.address_line_1

        address_line_2 = self.address_line_2

        city = self.city

        region = self.region

        postal_code = self.postal_code

        address_type = self.address_type

        display_name = self.display_name

        valid_from = self.valid_from

        valid_until = self.valid_until

        mode = self.mode

        standardized = self.standardized

        latitude = self.latitude

        longitude = self.longitude

        components = self.components

        country = self.country

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update({})
        if address_line_1 is not UNSET:
            field_dict["address_line_1"] = address_line_1
        if address_line_2 is not UNSET:
            field_dict["address_line_2"] = address_line_2
        if city is not UNSET:
            field_dict["city"] = city
        if region is not UNSET:
            field_dict["region"] = region
        if postal_code is not UNSET:
            field_dict["postal_code"] = postal_code
        if address_type is not UNSET:
            field_dict["address_type"] = address_type
        if display_name is not UNSET:
            field_dict["display_name"] = display_name
        if valid_from is not UNSET:
            field_dict["valid_from"] = valid_from
        if valid_until is not UNSET:
            field_dict["valid_until"] = valid_until
        if mode is not UNSET:
            field_dict["mode"] = mode
        if standardized is not UNSET:
            field_dict["standardized"] = standardized
        if latitude is not UNSET:
            field_dict["latitude"] = latitude
        if longitude is not UNSET:
            field_dict["longitude"] = longitude
        if components is not UNSET:
            field_dict["components"] = components
        if country is not UNSET:
            field_dict["country"] = country

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        address_line_1 = d.pop("address_line_1", UNSET)

        address_line_2 = d.pop("address_line_2", UNSET)

        city = d.pop("city", UNSET)

        region = d.pop("region", UNSET)

        postal_code = d.pop("postal_code", UNSET)

        address_type = d.pop("address_type", UNSET)

        display_name = d.pop("display_name", UNSET)

        valid_from = d.pop("valid_from", UNSET)

        valid_until = d.pop("valid_until", UNSET)

        mode = d.pop("mode", UNSET)

        standardized = d.pop("standardized", UNSET)

        latitude = d.pop("latitude", UNSET)

        longitude = d.pop("longitude", UNSET)

        components = d.pop("components", UNSET)

        country = d.pop("country", UNSET)

        body_address_create_admin_people_person_id_addresses_post = cls(
            address_line_1=address_line_1,
            address_line_2=address_line_2,
            city=city,
            region=region,
            postal_code=postal_code,
            address_type=address_type,
            display_name=display_name,
            valid_from=valid_from,
            valid_until=valid_until,
            mode=mode,
            standardized=standardized,
            latitude=latitude,
            longitude=longitude,
            components=components,
            country=country,
        )

        body_address_create_admin_people_person_id_addresses_post.additional_properties = d
        return body_address_create_admin_people_person_id_addresses_post

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
