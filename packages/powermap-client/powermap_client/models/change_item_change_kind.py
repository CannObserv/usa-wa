from enum import Enum


class ChangeItemChangeKind(str, Enum):
    DELETED = "deleted"
    UPDATED = "updated"

    def __str__(self) -> str:
        return str(self.value)
