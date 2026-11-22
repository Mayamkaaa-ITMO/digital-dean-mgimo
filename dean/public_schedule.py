"""Read-only published teaching data and the student's separate context."""
import hashlib
import json
import os
import re
from dataclasses import asdict, dataclass
from datetime import date, datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from . import services
from .db import write
from .domain import DomainError, field, require, role, timestamp
from .schedule import Lesson, ScheduleScope, local_midnight

DATA = Path(__file__).with_name("public_data")
TUSUR = json.loads((DATA / "tusur.json").read_text(encoding="utf-8"))
TUSUR_LIVE_WEEKS = json.loads((DATA / "tusur_live_weeks.json").read_text(encoding="utf-8"))
SOURCES = {source["source_id"]: source for source in (
    TUSUR, *json.loads((DATA / "university_sources.json").read_text(encoding="utf-8"))
)}
ROOM_PATH = re.compile(r"/buildings/([a-z]+[0-9]*)/auditoriums/([a-z0-9-]+)")
ROOM_REF = re.compile(r"ref:([a-z0-9-]{1,60})")
LABEL = re.compile(r"[а-яёa-z0-9-]{1,10} [а-яёa-z0-9-]{1,12}", re.IGNORECASE)
CONTACT = re.compile(r"@|https?://|\+\d[\d ()-]{8,}|\b[A-ZА-ЯЁ]\.\s*[A-ZА-ЯЁ]\.")


SOURCE_LABELS = {
    "tusur-timetable": "Расписание · Томск",
    "spbpu-ruz": "Расписание · Санкт-Петербург",
    "susu-timetable": "Расписание · Челябинск",
    "nspu-reference": "Справочник · Новосибирск",
    "guap-reference": "Справочник · Санкт-Петербург",
}


def source_label(source):
    if os.getenv("DEAN_PRODUCT") == "1":
        return SOURCE_LABELS[source["source_id"]]
    return source["name"]


@dataclass(frozen=True)
class Week:
    source_id: str
    group_id: str
    monday: date

    @property
    def key(self):
        return self.source_id, self.group_id, self.monday.isoformat()

    @property
    def group(self):
        return SOURCES[self.source_id]["groups"][self.group_id]

    @property
    def info(self):
        return known_weeks(self.source_id, self.group_id)[self.monday.isoformat()]

    @property
    def url(self):
        if "url" in self.info:
            return self.info["url"]
        return SOURCES[self.source_id]["origin"] + self.group["path"] + "?week_id=" + self.info["id"]


@dataclass(frozen=True)
class Event:
    external_id: str
    subject: str
    kind: str
    starts_at: str
    ends_at: str
    rooms: tuple
    cancelled: bool = False


def dump(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def known_weeks(source_id, group_id):
    source = SOURCES.get(source_id, {})
    group = source.get("groups", {}).get(group_id, {})
    snapshots = group.get("weeks", {})
    if source_id == TUSUR["source_id"] and group_id == TUSUR_LIVE_WEEKS["group"]:
        return {**snapshots, **TUSUR_LIVE_WEEKS["weeks"]}
    return snapshots


def week_scope(source_id, group_id, raw_date):
    if raw_date not in known_weeks(source_id, group_id):
        raise DomainError(422, "Неделя и группа должны быть из проверенного охвата источника.")
    return Week(source_id, group_id, date.fromisoformat(raw_date))


def lesson_id(week, external_id):
    payload = dump([*week.key, external_id]).encode()
    return hashlib.sha256(payload).hexdigest()[:32]


def normalize(week, records):
    """Transform the bounded, dedicated fields of the schedule grid."""
    source = SOURCES[week.source_id]
    zone = ZoneInfo(source["zone"])
    if len(records) > 150:
        raise ValueError("too_many_events")
    events = {}
    kinds = {"Лекция", "Практика", "Лабораторная работа", "Сам.Раб.", "Экзамен", "Зачёт", "Консультация", "Занятие"}
    for external_id, day, start, end, subject, kind, places in records:
        if not re.fullmatch(r"[0-9]{1,16}", external_id) or day not in week.info.get("days", range(1, 7)):
            raise ValueError("invalid_occurrence")
        if external_id in events:
            raise ValueError("ambiguous_identity")
        if kind not in kinds or not 1 <= len(subject) <= 160 or CONTACT.search(subject):
            raise ValueError("invalid_lesson_fields")
        if not all(re.fullmatch(r"\d{2}:\d{2}", value) for value in (start, end)):
            raise ValueError("invalid_time")
        local_day = week.monday + timedelta(days=day - 1)
        starts = datetime.combine(local_day, time.fromisoformat(start), zone)
        ends = datetime.combine(local_day, time.fromisoformat(end), zone)
        if ends <= starts:
            raise ValueError("invalid_interval")
        if len(places) > 12 or any(not valid_room(source, label, path) for label, path in places):
            raise ValueError("invalid_room")
        rooms = tuple(sorted({(label, path) for label, path in places}, key=lambda room: (room[1], room[0])))
        events[external_id] = Event(external_id, subject, kind, timestamp(starts), timestamp(ends), rooms)
    return tuple(events[key] for key in sorted(events))


def sample_events(week):
    source = SOURCES[week.source_id]
    filename = source.get("sample_file", "tusur_schedule_sample.json")
    sample = json.loads((DATA / filename).read_text(encoding="utf-8"))
    return normalize(week, sample[week.group_id][week.monday.isoformat()])


def valid_room(source, label, path):
    if source["source_id"] == TUSUR["source_id"]:
        return bool(LABEL.fullmatch(label) and ROOM_PATH.fullmatch(path))
    record = ROOM_REF.fullmatch(path)
    if record:
        return source.get("room_cards", {}).get(record[1], {}).get("label") == label
    return not path and bool(re.fullmatch(r"[\w /().–-]{1,65}", label))


def covered_day(selection, day, publication):
    if publication is None:
        return False
    week = week_scope(*selection, publication["monday"])
    return day.weekday() + 1 in week.info.get("days", range(1, 7))


def live_available(source_id):
    return source_id == TUSUR["source_id"] and (
        TUSUR["live_enabled"] or os.getenv("DEAN_TUSUR_LIVE") == "1")


def context(db, user):
    if user["role"] != "student":
        return None
    row = db.execute("SELECT source_id, group_id FROM public_selection WHERE owner_id=?", (user["id"],)).fetchone()
    if row:
        source = SOURCES.get(row["source_id"])
        if source and row["group_id"] in source["groups"]:
            return row["source_id"], row["group_id"]
    return None


def set_selection(db, user, source_id, group_id):
    role(user, "student")
    if source_id or group_id:
        group = SOURCES.get(source_id, {}).get("groups", {}).get(group_id)
        if group is None:
            raise DomainError(422, "Выбери опубликованную группу из списка.")
        db.execute("""
            INSERT INTO public_selection VALUES (?,?,?)
            ON CONFLICT(owner_id) DO UPDATE SET source_id=excluded.source_id, group_id=excluded.group_id
        """, (user["id"], source_id, group_id))
    else:
        db.execute("DELETE FROM public_selection WHERE owner_id=?", (user["id"],))


def choose(db, user, source_id, group_id):
    with write(db):
        set_selection(db, user, source_id, group_id)


def scope(selection):
    source_id, group_id = selection
    source = SOURCES[source_id]
    return ScheduleScope(source["institution_id"], group_id, source["academic_year"], source["zone"])


def public_week(db, selection, day):
    monday = day - timedelta(days=day.weekday())
    return db.execute("""
        SELECT * FROM public_week WHERE source_id=? AND group_id=? AND monday=? AND revision>0
    """, (*selection, monday.isoformat())).fetchone()


def public_weeks(db, selection, first, last):
    first_monday = first - timedelta(days=first.weekday())
    last_monday = last - timedelta(days=last.weekday())
    rows = db.execute("""
        SELECT * FROM public_week WHERE source_id=? AND group_id=? AND monday BETWEEN ? AND ? AND revision>0
    """, (*selection, first_monday.isoformat(), last_monday.isoformat()))
    return {date.fromisoformat(row["monday"]): row for row in rows}


def local_lesson(db, selection, lesson):
    if selection is None:
        raise DomainError(404, "Сначала выбери публичный учебный контекст.")
    return require(db.execute("""
        SELECT e.*, w.provenance, w.observed_at, w.published_at FROM public_lesson e
        JOIN public_week w USING(source_id,group_id,monday)
        WHERE e.source_id=? AND e.group_id=? AND e.id=?
    """, (*selection, lesson)).fetchone())


class Provider:
    def __init__(self, db, selection):
        self.db = db
        self.selection = selection

    def to_lesson(self, period, row):
        week = week_scope(*self.selection, row["monday"])
        rooms = ", ".join(room[0] for room in json.loads(row["rooms_json"]))
        return Lesson(row["id"], period.university_id, period.group_id, period.period_id, row["subject"],
                      row["kind"], row["starts_at"], row["ends_at"], period.timezone, rooms or None, None,
                      self.selection[0], row["external_id"], "public", week.url, row["observed_at"],
                      row["version"], row["subject"])

    def occurrences(self, period, start, stop):
        if period != scope(self.selection):
            return []
        rows = self.db.execute("""
            SELECT e.*, w.observed_at FROM public_lesson e JOIN public_week w USING(source_id,group_id,monday)
            WHERE e.source_id=? AND e.group_id=? AND e.cancelled=0 AND e.starts_at>=? AND e.starts_at<?
            ORDER BY e.starts_at, e.id
        """, (*self.selection, local_midnight(start, period.timezone), local_midnight(stop, period.timezone)))
        return [self.to_lesson(period, row) for row in rows]

    def occurrence(self, period, event_id):
        if period != scope(self.selection):
            return None
        return self.to_lesson(period, local_lesson(self.db, self.selection, event_id))


def note(db, user, lesson):
    return db.execute("SELECT * FROM public_note WHERE owner_id=? AND lesson_id=?", (user["id"], lesson)).fetchone()


def save_note(db, user, selection, lesson, body, remind, key):
    role(user, "student")
    body = field(body, "Заметка", 1600, 0)
    local_lesson(db, selection, lesson)

    def apply():
        local_lesson(db, context(db, user), lesson)
        db.execute("""
            INSERT INTO public_note VALUES (?,?,?,?,?)
            ON CONFLICT(owner_id,lesson_id) DO UPDATE SET body=excluded.body, remind=excluded.remind,
                updated_at=excluded.updated_at
        """, (user["id"], lesson, body, int(remind), timestamp()))
        if not remind:
            db.execute("""
                UPDATE notification SET active=0 WHERE student_id=? AND kind='public_reminder' AND target_id=?
            """, (user["id"], lesson))
        return lesson

    return services.command(db, user, "public.note", key, [lesson, body, remind], apply)


def event_json(events):
    return dump([asdict(event) for event in events])
