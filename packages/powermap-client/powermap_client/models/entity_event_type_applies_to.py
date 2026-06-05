from enum import Enum


class EntityEventTypeAppliesTo(str, Enum):
    BOTH = "both"
    ORGANIZATION = "organization"
    PERSON = "person"

    def __str__(self) -> str:
        return str(self.value)
