from enum import Enum


class SubscriptionItemEntityType(str, Enum):
    JURISDICTION = "jurisdiction"
    ORGANIZATION = "organization"
    PERSON = "person"
    ROLE = "role"
    ROLE_ASSIGNMENT = "role_assignment"

    def __str__(self) -> str:
        return str(self.value)
