from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..models.observation_contact_method_contact_type import ObservationContactMethodContactType
from ..types import UNSET, Unset

T = TypeVar("T", bound="ObservationContactMethod")


@_attrs_define
class ObservationContactMethod:
    """A contact method claim (email or phone).

    Attributes:
        contact_type (ObservationContactMethodContactType):
        value (str):
        display_label (None | str | Unset):
    """

    contact_type: ObservationContactMethodContactType
    value: str
    display_label: None | str | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        contact_type = self.contact_type.value

        value = self.value

        display_label: None | str | Unset
        if isinstance(self.display_label, Unset):
            display_label = UNSET
        else:
            display_label = self.display_label

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "contact_type": contact_type,
                "value": value,
            }
        )
        if display_label is not UNSET:
            field_dict["display_label"] = display_label

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        contact_type = ObservationContactMethodContactType(d.pop("contact_type"))

        value = d.pop("value")

        def _parse_display_label(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        display_label = _parse_display_label(d.pop("display_label", UNSET))

        observation_contact_method = cls(
            contact_type=contact_type,
            value=value,
            display_label=display_label,
        )

        observation_contact_method.additional_properties = d
        return observation_contact_method

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
