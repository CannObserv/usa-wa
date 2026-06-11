from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar

from attrs import define as _attrs_define
from attrs import field as _attrs_field

if TYPE_CHECKING:
    from ..models.org_affiliation_type import OrgAffiliationType


T = TypeVar("T", bound="OrgJurisdictionAffiliation")


@_attrs_define
class OrgJurisdictionAffiliation:
    """A single typed association between an org and a jurisdiction.

    Attributes:
        jurisdiction_id (str):
        affiliation_type (OrgAffiliationType): A type of org-jurisdiction affiliation from the lookup table.
    """

    jurisdiction_id: str
    affiliation_type: OrgAffiliationType
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        jurisdiction_id = self.jurisdiction_id

        affiliation_type = self.affiliation_type.to_dict()

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "jurisdiction_id": jurisdiction_id,
                "affiliation_type": affiliation_type,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.org_affiliation_type import OrgAffiliationType

        d = dict(src_dict)
        jurisdiction_id = d.pop("jurisdiction_id")

        affiliation_type = OrgAffiliationType.from_dict(d.pop("affiliation_type"))

        org_jurisdiction_affiliation = cls(
            jurisdiction_id=jurisdiction_id,
            affiliation_type=affiliation_type,
        )

        org_jurisdiction_affiliation.additional_properties = d
        return org_jurisdiction_affiliation

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
