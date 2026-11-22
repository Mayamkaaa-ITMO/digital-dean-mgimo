from contextlib import contextmanager
from contextvars import ContextVar
from datetime import UTC, datetime

_override = ContextVar("dean_clock", default=None)


def now():
    return _override.get() or datetime.now(UTC)


@contextmanager
def at(moment):
    if moment.utcoffset() is None:
        raise ValueError("The application clock requires an aware datetime")
    token = _override.set(moment.astimezone(UTC))
    try:
        yield
    finally:
        _override.reset(token)
