from enum import Enum


class ObservationAddressAddressType(str, Enum):
    MAILING = "mailing"
    OTHER = "other"
    PHYSICAL = "physical"

    def __str__(self) -> str:
        return str(self.value)
