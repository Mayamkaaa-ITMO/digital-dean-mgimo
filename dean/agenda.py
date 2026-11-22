from dataclasses import dataclass
import json

from . import calendar_tools, meetings, public_schedule, reservations
from .schedule import local_midnight, student_scope
from .workspace import personal_occurrences, scope

TYPES = {"lesson": "Занятия", "exam": "Экзамены", "task": "Сроки задач", "event": "Личные события", "booking": "Записи и брони"}


@dataclass(frozen=True)
class Entry:
    key: str
    title: str
    start: str
    end: str | None
    kind: str
    href: str
    subtitle: str = ""
    subject_id: str | None = None
    all_day: bool = False
    changed: bool = False
    lesson_kind: str | None = None
    room: str = ""
    teacher: str = ""
    version: int = 1
    rooms: tuple[str, ...] = ()
    lesson_status: str = ""


def entries(db, user, provider, first_day, after_last_day, selection=None):
    period = public_schedule.scope(selection) if selection else student_scope(user)
    since = local_midnight(first_day, period.timezone)
    until = local_midnight(after_last_day, period.timezone)
    calendar = []
    if selection:
        for row in calendar_tools.public_events(db, user["id"], selection, since, until):
            adjusted = row["base_version"] == row["version"]
            start = row["local_start"] if adjusted else row["starts_at"]
            end = row["local_end"] if adjusted else row["ends_at"]
            if end <= since or start >= until:
                continue
            rooms = tuple(room[0] for room in json.loads(row["rooms_json"]))
            room = row["local_room"] if adjusted else ", ".join(rooms)
            detail = " · ".join((row["kind"], room or "Аудитория не указана"))
            status = ""
            if adjusted:
                status = "Личная поправка"
            elif row["base_version"] is not None:
                status = "Поправка ждёт сверки"
            if status:
                detail += " · " + status
            kind = "exam" if row["kind"] == "Экзамен" else "lesson"
            calendar.append(Entry(f"lesson:{row['id']}", row["subject"], start, end, kind,
                                  f"/schedule/public/{row['id']}", detail, row["subject"], changed=row["version"] > 1,
                                  lesson_kind=row["kind"], room=room, version=row["version"], rooms=rooms,
                                  lesson_status=status))
    else:
        for lesson in provider.occurrences(period, first_day, after_last_day):
            details = " · ".join((lesson.kind, lesson.room or "Аудитория не указана",
                                  lesson.teacher or "Преподаватель не указан"))
            calendar.append(Entry(f"lesson:{lesson.id}", lesson.subject, lesson.starts_at, lesson.ends_at, "lesson",
                                  f"/schedule/{lesson.id}", details, lesson.subject_id, changed=lesson.version > 1,
                                  lesson_kind=lesson.kind, room=lesson.room or "", teacher=lesson.teacher or "",
                                  version=lesson.version, rooms=(lesson.room,) if lesson.room else ()))
    for row, day, start, end in personal_occurrences(db, user, since, until):
        calendar.append(Entry(f"event:{row['id']}:{day}", row["title"], start, end, "event",
                              f"/events/{row['id']}?occurrence={day}", row["location"] or "Личное событие",
                              all_day=bool(row["all_day"])))
    if not selection:
        for row in db.execute("""
            SELECT r.id, r.subject_id, r.exam_at, s.name FROM learning_result r JOIN subject s
            ON s.university_id=r.university_id AND s.id=r.subject_id
            WHERE r.university_id=? AND r.student_id=? AND r.exam_at>=? AND r.exam_at<?
        """, (*scope(user), since, until)):
            calendar.append(Entry(f"exam:{row['id']}", row["name"], row["exam_at"], None, "exam",
                                  f"/grades/{row['id']}", "Экзамен", row["subject_id"]))
    for row in db.execute("""
        SELECT id, title, subject_id, due_at FROM task WHERE university_id=? AND owner_id=?
        AND state='todo' AND due_at>=? AND due_at<?
        AND NOT EXISTS (SELECT 1 FROM team_task t WHERE t.task_id=task.id)
    """, (*scope(user), since, until)):
        calendar.append(Entry(f"task:{row['id']}", row["title"], row["due_at"], None, "task",
                              f"/tasks/{row['id']}", "Срок задачи", row["subject_id"]))
    for row in reservations.bookings(db, user, active_only=True, start=since, end=until):
        calendar.append(Entry(f"booking:{row['id']}", row["title"], row["starts_at"], row["ends_at"], "booking",
                              f"/bookings/{row['id']}", row["place"]))
    for row in meetings.calendar(db, user, since, until):
        calendar.append(Entry(f"meeting:{row['id']}", row["title"], row["starts_at"], row["ends_at"], "event",
                              f"/teams/meetings/{row['id']}", row["team_name"], changed=row["version"] > 1))
    for row in db.execute("""
        SELECT t.*,g.team_id,c.title AS team_title FROM team_task g JOIN task t ON t.id=g.task_id
        JOIN team c ON c.id=g.team_id AND c.university_id=t.university_id
        JOIN team_member m ON m.team_id=g.team_id AND m.student_id=g.assignee_id AND m.removed_at IS NULL
        WHERE t.university_id=? AND g.assignee_id=? AND t.state='todo' AND t.due_at>=? AND t.due_at<?
    """, (*scope(user), since, until)):
        calendar.append(Entry(f"team-task:{row['id']}", row["title"], row["due_at"], None, "task",
                              f"/teams/{row['team_id']}/tasks/{row['id']}", row["team_title"]))
    for row in db.execute("""
        SELECT e.*,u.title FROM equipment_loan e JOIN equipment_unit u ON u.id=e.unit_id
        AND u.university_id=e.university_id WHERE e.university_id=? AND e.student_id=?
        AND e.state IN ('reserved','issued') AND e.ends_at>=? AND e.starts_at<?
    """, (*scope(user), since, until)):
        href = f"/equipment/loans/{row['id']}"
        if since <= row["starts_at"] < until and row["state"] == "reserved":
            calendar.append(Entry(f"equipment:{row['id']}:pickup", "Получить технику · " + row["title"],
                                  row["starts_at"], None, "booking", href, row["pickup"]))
        if since <= row["ends_at"] < until:
            calendar.append(Entry(f"equipment:{row['id']}:return", "Вернуть технику · " + row["title"],
                                  row["ends_at"], None, "booking", href, row["return_place"]))
    for row in db.execute("""
        SELECT c.id,c.due_at,o.title FROM practice_case c JOIN opportunity o ON o.id=c.offer_id
        AND o.university_id=c.university_id WHERE c.university_id=? AND c.student_id=?
        AND c.state IN ('active','review','changes') AND c.due_at>=? AND c.due_at<?
    """, (*scope(user), since, until)):
        calendar.append(Entry(f"practice:{row['id']}", "Отчёт по практике · " + row["title"],
                              row["due_at"], None, "task", f"/practice/{row['id']}", "Срок практики"))
    return sorted(calendar, key=lambda item: (item.start, item.key))


def conflicts(calendar):
    flagged = set()
    active = []
    for item in calendar:
        if item.end is None:
            continue
        active = [previous for previous in active if previous.end > item.start]
        for previous in active:
            flagged.update((previous.key, item.key))
        active.append(item)
    return flagged
