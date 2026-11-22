from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from typing import Protocol
from zoneinfo import ZoneInfo

from .clock import now
from .domain import DomainError, require, role, timestamp


@dataclass(frozen=True)
class ScheduleScope:
    university_id: str
    group_id: str
    period_id: str
    timezone: str


@dataclass(frozen=True)
class Lesson:
    id: str
    university_id: str
    group_id: str
    period_id: str
    subject: str
    kind: str
    starts_at: str
    ends_at: str
    timezone: str
    room: str | None
    teacher: str | None
    source_id: str
    source_event_id: str | None
    origin: str
    source_url: str | None
    observed_at: str | None
    version: int
    subject_id: str | None = None


class ScheduleProvider(Protocol):
    def occurrences(self, scope: ScheduleScope, start: date, stop: date) -> list[Lesson]:
        """Return resolved occurrences for the half-open interval in campus dates."""

    def occurrence(self, scope: ScheduleScope, event_id: str) -> Lesson | None:
        """Return a resolved occurrence restricted to the supplied scope."""


SELECT = """
    SELECT e.*, s.name AS subject FROM schedule_event e
    JOIN subject s ON s.university_id = e.university_id AND s.id = e.subject_id
"""


def scope_key(scope):
    return scope.university_id, scope.group_id, scope.period_id


class SQLiteScheduleProvider:
    def __init__(self, db):
        self.db = db

    @staticmethod
    def lesson(row):
        return Lesson(**{key: row[key] for key in Lesson.__dataclass_fields__})

    def occurrences(self, scope, start, stop):
        zone = ZoneInfo(scope.timezone)
        rows = self.db.execute(SELECT + """
            WHERE e.university_id = ? AND e.group_id = ? AND e.period_id = ?
              AND e.ends_at > ? AND e.starts_at < ?
            ORDER BY e.starts_at, e.id
        """, (*scope_key(scope), timestamp(datetime.combine(start, time.min, zone)),
              timestamp(datetime.combine(stop, time.min, zone))))
        return [self.lesson(row) for row in rows]

    def occurrence(self, scope, event_id):
        row = self.db.execute(SELECT + """
            WHERE e.university_id = ? AND e.group_id = ? AND e.period_id = ? AND e.id = ?
        """, (*scope_key(scope), event_id)).fetchone()
        return self.lesson(row) if row else None


def student_scope(user):
    role(user, "student")
    return ScheduleScope(user["university_id"], user["group_id"], user["period_id"], user["timezone"])


def staff_lessons(db, user, event_id=None, *, week=None):
    role(user, "staff")
    query = SELECT + """
        JOIN staff_group g ON g.university_id = e.university_id AND g.group_id = e.group_id
        WHERE g.university_id = ? AND g.staff_id = ? AND e.origin = 'demo_university'
    """
    params = (user["university_id"], user["id"])
    if event_id is not None:
        return require(db.execute(query + " AND e.id = ?", (*params, event_id)).fetchone())
    focus = today_for(user) if week is None else week
    first = focus - timedelta(days=focus.weekday())
    stop = first + timedelta(days=28 if week is None else 7)
    return list(db.execute(query + """
        AND e.starts_at >= ? AND e.starts_at < ? ORDER BY e.starts_at, e.id
    """, (*params, local_midnight(first, user["timezone"]),
          local_midnight(stop, user["timezone"]))))


def local_value(value, zone):
    return datetime.fromisoformat(value).astimezone(ZoneInfo(zone))


def today_for(user):
    return now().astimezone(ZoneInfo(user["timezone"])).date()


def local_midnight(day, zone):
    return timestamp(datetime.combine(day, time.min, ZoneInfo(zone)))


def edit_time(value, zone_name):
    try:
        naive = datetime.strptime(value, "%Y-%m-%dT%H:%M")
        zone = ZoneInfo(zone_name)
        candidates = {
            candidate.astimezone(UTC)
            for fold in (0, 1)
            if (candidate := naive.replace(tzinfo=zone, fold=fold))
            .astimezone(UTC).astimezone(zone).replace(tzinfo=None) == naive
        }
        if len(candidates) == 1:
            return candidates.pop()
    except (OverflowError, TypeError, ValueError):
        pass
    raise DomainError(422, "Укажи однозначные дату и время занятия в часовом поясе вуза.")
