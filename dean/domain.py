from datetime import UTC

from .clock import now
from uuid import uuid4


class DomainError(Exception):
    def __init__(self, status, message):
        super().__init__(message)
        self.status = status
        self.message = message


def require(record):
    if record is None:
        raise DomainError(404, "Запись недоступна или не найдена.")
    return record


def role(user, expected):
    if user["role"] != expected:
        raise DomainError(403, "Для этого действия нужна другая роль.")


def identifier():
    return uuid4().hex


def timestamp(moment=None):
    return (moment or now()).astimezone(UTC).isoformat(timespec="seconds")


def field(value, name, limit, minimum=1):
    value = value.strip()
    if not minimum <= len(value) <= limit:
        raise DomainError(422, f"Поле «{name}» должно содержать от {minimum} до {limit} символов.")
    return value
