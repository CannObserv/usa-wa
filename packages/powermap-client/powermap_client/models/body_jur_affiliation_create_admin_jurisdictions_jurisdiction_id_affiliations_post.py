from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..types import UNSET, Unset

T = TypeVar("T", bound="BodyJurAffiliationCreateAdminJurisdictionsJurisdictionIdAffiliationsPost")


@_attrs_define
class BodyJurAffiliationCreateAdminJurisdictionsJurisdictionIdAffiliationsPost:
    """
    Attributes:
        organization_id (str | Unset):  Default: ''.
        affiliation_type_id (str | Unset):  Default: ''.
    """

    organization_id: str | Unset = ""
    affiliation_type_id: str | Unset = ""
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        organization_id = self.organization_id

        affiliation_type_id = self.affiliation_type_id

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update({})
        if organization_id is not UNSET:
            field_dict["organization_id"] = organization_id
        if affiliation_type_id is not UNSET:
            field_dict["affiliation_type_id"] = affiliation_type_id

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        organization_id = d.pop("organization_id", UNSET)

        affiliation_type_id = d.pop("affiliation_type_id", UNSET)

        body_jur_affiliation_create_admin_jurisdictions_jurisdiction_id_affiliations_post = cls(
            organization_id=organization_id,
            affiliation_type_id=affiliation_type_id,
        )

        body_jur_affiliation_create_admin_jurisdictions_jurisdiction_id_affiliations_post.additional_properties = d
        return body_jur_affiliation_create_admin_jurisdictions_jurisdiction_id_affiliations_post

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
