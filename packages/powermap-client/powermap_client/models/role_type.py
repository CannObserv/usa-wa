from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar

from attrs import define as _attrs_define
from attrs import field as _attrs_field

T = TypeVar("T", bound="RoleType")


@_attrs_define
class RoleType:
    """A role-type classifier (office) — the structural-match vocabulary (#268).

    ``slug`` is the stable value a producer sends as ``RoleObservationRequest.
    role_type`` and reads back on ``RoleDetail.role_type_slug``.
    ``expects_jurisdiction`` is an advisory hint that this office is normally
    attached with a jurisdiction (structural-tuple match); it is not enforced by
    ``resolve_role``. ``requires_qualifier`` (#273) IS enforced: a jurisdictional
    observation of such an office without a ``qualifier`` is rejected
    (``qualifier_required``) rather than minting a positionless seat.

        Attributes:
            id (str):
            slug (str):
            display_name (str):
            expects_jurisdiction (bool):
            requires_qualifier (bool):
    """

    id: str
    slug: str
    display_name: str
    expects_jurisdiction: bool
    requires_qualifier: bool
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        id = self.id

        slug = self.slug

        display_name = self.display_name

        expects_jurisdiction = self.expects_jurisdiction

        requires_qualifier = self.requires_qualifier

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "id": id,
                "slug": slug,
                "display_name": display_name,
                "expects_jurisdiction": expects_jurisdiction,
                "requires_qualifier": requires_qualifier,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        id = d.pop("id")

        slug = d.pop("slug")

        display_name = d.pop("display_name")

        expects_jurisdiction = d.pop("expects_jurisdiction")

        requires_qualifier = d.pop("requires_qualifier")

        role_type = cls(
            id=id,
            slug=slug,
            display_name=display_name,
            expects_jurisdiction=expects_jurisdiction,
            requires_qualifier=requires_qualifier,
        )

        role_type.additional_properties = d
        return role_type

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
