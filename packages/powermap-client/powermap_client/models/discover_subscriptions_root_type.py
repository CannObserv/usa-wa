from enum import Enum


class DiscoverSubscriptionsRootType(str, Enum):
    JURISDICTION = "jurisdiction"
    ORGANIZATION = "organization"

    def __str__(self) -> str:
        return str(self.value)
