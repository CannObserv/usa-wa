from enum import Enum


class ObservationEventItemOp(str, Enum):
    OBSERVE = "observe"
    RETRACT = "retract"

    def __str__(self) -> str:
        return str(self.value)
