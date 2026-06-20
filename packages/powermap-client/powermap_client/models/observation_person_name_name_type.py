from enum import Enum


class ObservationPersonNameNameType(str, Enum):
    ALIAS = "alias"
    DEADNAME = "deadname"
    FORMER = "former"
    INITIALS = "initials"
    LEGAL = "legal"
    MAIDEN = "maiden"
    MRZ = "mrz"
    PREFERRED = "preferred"
    READING = "reading"
    RELIGIOUS = "religious"
    ROMANIZATION = "romanization"
    STAGE = "stage"
    VARIANT = "variant"

    def __str__(self) -> str:
        return str(self.value)
