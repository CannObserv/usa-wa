from enum import Enum


class ContactNewRowAdminOrgsEntityIdContactsNewRowGetContactType(str, Enum):
    EMAIL = "email"
    PHONE = "phone"

    def __str__(self) -> str:
        return str(self.value)
