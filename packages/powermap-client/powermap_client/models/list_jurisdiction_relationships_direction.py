from enum import Enum


class ListJurisdictionRelationshipsDirection(str, Enum):
    BOTH = "both"
    FROM = "from"
    TO = "to"

    def __str__(self) -> str:
        return str(self.value)
