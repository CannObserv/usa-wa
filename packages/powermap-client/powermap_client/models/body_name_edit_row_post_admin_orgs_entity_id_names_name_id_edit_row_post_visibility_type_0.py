from enum import Enum


class BodyNameEditRowPostAdminOrgsEntityIdNamesNameIdEditRowPostVisibilityType0(str, Enum):
    HIDDEN = "hidden"
    LEGAL_ONLY = "legal_only"
    PUBLIC = "public"

    def __str__(self) -> str:
        return str(self.value)
