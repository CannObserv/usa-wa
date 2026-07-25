from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..types import UNSET, Unset

T = TypeVar("T", bound="BodyOrgAffiliationCreateAdminOrgsOrgIdJurisdictionAffiliationsPost")


@_attrs_define
class BodyOrgAffiliationCreateAdminOrgsOrgIdJurisdictionAffiliationsPost:
    """
    Attributes:
        jurisdiction_id (str | Unset):  Default: ''.
        affiliation_type_id (str | Unset):  Default: ''.
    """

    jurisdiction_id: str | Unset = ""
    affiliation_type_id: str | Unset = ""
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        jurisdiction_id = self.jurisdiction_id

        affiliation_type_id = self.affiliation_type_id

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update({})
        if jurisdiction_id is not UNSET:
            field_dict["jurisdiction_id"] = jurisdiction_id
        if affiliation_type_id is not UNSET:
            field_dict["affiliation_type_id"] = affiliation_type_id

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        jurisdiction_id = d.pop("jurisdiction_id", UNSET)

        affiliation_type_id = d.pop("affiliation_type_id", UNSET)

        body_org_affiliation_create_admin_orgs_org_id_jurisdiction_affiliations_post = cls(
            jurisdiction_id=jurisdiction_id,
            affiliation_type_id=affiliation_type_id,
        )

        body_org_affiliation_create_admin_orgs_org_id_jurisdiction_affiliations_post.additional_properties = d
        return body_org_affiliation_create_admin_orgs_org_id_jurisdiction_affiliations_post

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
