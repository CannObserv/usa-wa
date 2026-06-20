from enum import Enum


class ObservationOrgNameNameType(str, Enum):
    DBA = "dba"
    FORMER = "former"
    LEGAL = "legal"

    def __str__(self) -> str:
        return str(self.value)
