from enum import Enum


class EntityEventLinkedEntityTypeType0(str, Enum):
    ORGANIZATION = "organization"
    PERSON = "person"

    def __str__(self) -> str:
        return str(self.value)
