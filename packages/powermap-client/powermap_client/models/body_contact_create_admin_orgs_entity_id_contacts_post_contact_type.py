from enum import Enum


class BodyContactCreateAdminOrgsEntityIdContactsPostContactType(str, Enum):
    EMAIL = "email"
    PHONE = "phone"

    def __str__(self) -> str:
        return str(self.value)
