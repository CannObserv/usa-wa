from enum import Enum


class ObservationNamePartsPrimaryIdentifierType0(str, Enum):
    FAMILY = "family"
    GIVEN = "given"
    MONONYM = "mononym"
    PATRONYMIC = "patronymic"

    def __str__(self) -> str:
        return str(self.value)
