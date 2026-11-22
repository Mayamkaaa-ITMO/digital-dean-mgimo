from dataclasses import dataclass
from datetime import timedelta

from . import activity_extensions, finances, library_queue, public_schedule, services as svc, workspace
from .clock import now
from .db import write
from .domain import timestamp
from .schedule import local_value

OPTIONAL = {
    "exam": "Экзамены за 7 дней",
    "task": "Сроки задач за 24 часа",
    "event": "Личные события за 24 часа",
}


@dataclass(frozen=True)
class Reminder:
    kind: str
    target: str
    key: str
    title: str
    body: str


def settings(db, user):
    saved = dict(db.execute("""
        SELECT kind, enabled FROM reminder_setting WHERE university_id=? AND student_id=?
    """, workspace.scope(user)))
    return {kind: bool(saved.get(kind, 1)) for kind in OPTIONAL}


def save_settings_in_transaction(db, user, chosen):
    db.executemany("""
        INSERT INTO reminder_setting VALUES (?, ?, ?, ?)
        ON CONFLICT(university_id, student_id, kind) DO UPDATE SET enabled=excluded.enabled
    """, [(*workspace.scope(user), kind, int(kind in chosen)) for kind in OPTIONAL])


def save_settings(db, user, chosen):
    with write(db):
        save_settings_in_transaction(db, user, chosen)


def due(db, user, current):
    scope = workspace.scope(user)
    active = settings(db, user)
    start, tomorrow = timestamp(current), timestamp(current + timedelta(days=1))
    zone = user["timezone"]

    def item(kind, target, when, label, subject):
        detail = local_value(when, zone).strftime("%d.%m, %H:%M")
        return Reminder(kind, target, f"{kind}:{target}:{when}", label, f"{subject} · {detail}")

    if active["exam"]:
        for row in db.execute("""
            SELECT r.id, r.exam_at, s.name FROM learning_result r JOIN subject s
            ON s.id=r.subject_id AND s.university_id=r.university_id
            WHERE r.university_id=? AND r.student_id=? AND r.score IS NULL AND r.outcome IS NULL
              AND r.exam_at>? AND r.exam_at<=?
        """, (*scope, start, timestamp(current + timedelta(days=7)))):
            yield item("exam", row["id"], row["exam_at"], "Приближается экзамен", row["name"])
    if active["task"]:
        for row in db.execute("""
            SELECT id, title, due_at FROM task WHERE university_id=? AND owner_id=?
            AND state='todo' AND due_at>? AND due_at<=?
            AND NOT EXISTS (SELECT 1 FROM team_task t WHERE t.task_id=task.id)
        """, (*scope, start, tomorrow)):
            yield item("task", row["id"], row["due_at"], "Скоро срок задачи", row["title"])
    if active["event"]:
        horizon = timestamp(current + timedelta(days=1, seconds=1))
        for row, _, begins, _ in workspace.personal_occurrences(db, user, start, horizon):
            if start < begins <= tomorrow:
                yield item("event", row["id"], begins, "Скоро личное событие", row["title"])


def library_due(db, user, current):
    since = timestamp(current)
    horizon = timestamp(current + timedelta(days=2))
    for row in db.execute("""
        SELECT l.id,l.due_at,b.title FROM library_loan l JOIN library_copy c
        ON c.id=l.copy_id AND c.university_id=l.university_id
        JOIN library_book b ON b.id=c.book_id AND b.university_id=c.university_id
        WHERE l.university_id=? AND l.student_id=? AND l.returned_at IS NULL AND l.due_at>? AND l.due_at<=?
    """, (*workspace.scope(user),since,horizon)):
        date = local_value(row["due_at"], user["timezone"]).strftime("%d.%m, %H:%M")
        yield Reminder("library",row["id"],f"library-due:{row['id']}:{row['due_at']}",
                       "Скоро вернуть книгу", f"{row['title']} · {date}")


def public_due(db, user, current):
    for row in db.execute("""
        SELECT p.* FROM public_note n JOIN public_lesson p ON p.id=n.lesson_id
        WHERE n.owner_id=? AND n.remind=1 AND p.cancelled=0 AND p.starts_at>? AND p.starts_at<=?
    """, (user["id"], timestamp(current), timestamp(current + timedelta(minutes=30)))):
        zone = public_schedule.SOURCES[row["source_id"]]["zone"]
        text = local_value(row["starts_at"], zone).strftime("%d.%m, %H:%M")
        yield Reminder("public_reminder", row["id"], f"public-reminder:{row['id']}:{row['starts_at']}",
                       "Скоро публичная пара", f"{row['subject']} · {text}")


def source_is_current(db, user, notification):
    kind, target = notification["kind"], notification["target_id"]
    scope = workspace.scope(user)
    if kind in OPTIONAL and not settings(db,user)[kind]:
        return False
    if kind == "exam":
        row = db.execute("""
            SELECT exam_at FROM learning_result WHERE university_id=? AND student_id=? AND id=?
            AND score IS NULL AND outcome IS NULL
        """, (*scope,target)).fetchone()
        return bool(row and notification["event_key"] == f"exam:{target}:{row['exam_at']}")
    if kind == "task":
        row = db.execute("""
            SELECT due_at FROM task WHERE university_id=? AND owner_id=? AND id=? AND state='todo'
        """, (*scope,target)).fetchone()
        return bool(row and notification["event_key"] == f"task:{target}:{row['due_at']}")
    if kind == "library":
        row = db.execute("""
            SELECT due_at FROM library_loan WHERE university_id=? AND student_id=? AND id=? AND returned_at IS NULL
        """, (*scope,target)).fetchone()
        return bool(row and notification["event_key"] == f"library-due:{target}:{row['due_at']}")
    if kind == "public_reminder":
        row = db.execute("""
            SELECT p.starts_at FROM public_note n JOIN public_lesson p ON p.id=n.lesson_id
            WHERE n.owner_id=? AND n.remind=1 AND p.id=? AND p.cancelled=0 AND p.starts_at>?
        """, (user["id"], target, timestamp())).fetchone()
        return bool(row and notification["event_key"] == f"public-reminder:{target}:{row['starts_at']}")
    row = db.execute("SELECT * FROM personal_event WHERE university_id=? AND owner_id=? AND id=?",
                     (*scope,target)).fetchone()
    if row is None:
        return False
    skipped = {item[0] for item in db.execute("SELECT occurrence_day FROM personal_event_skip WHERE event_id=?", (target,))}
    return any(notification["event_key"] == f"event:{target}:{start}" and day not in skipped
               for day,start,_ in workspace.occurrences(row,user["timezone"]))


def refresh_in_transaction(db, user, current):
    wanted = {item.key: item for item in (*due(db, user, current), *library_due(db, user, current),
                                         *public_due(db, user, current),
                                         *activity_extensions.due_reminders(db, user, current))}
    scope = workspace.scope(user)
    existing = {row["event_key"]: row for row in db.execute("""
        SELECT id, event_key, kind, target_id, active, body FROM notification
        WHERE university_id=? AND student_id=? AND (kind IN ('exam','task','event','public_reminder')
              OR event_key LIKE 'library-due:%' OR event_key LIKE 'activity-reminder:%')
    """, scope)}
    for key, row in existing.items():
        item = wanted.get(key)
        if item is not None:
            if not row["active"] or row["body"] != item.body:
                db.execute("UPDATE notification SET active=1, body=? WHERE id=?", (item.body,row["id"]))
        else:
            active = int(not key.startswith(activity_extensions.REMINDER_PREFIX) and source_is_current(db,user,row))
            if active != row["active"]:
                db.execute("UPDATE notification SET active=? WHERE id=?", (active,row["id"]))
    for key, item in wanted.items():
        if key not in existing:
            svc.notify(db, *scope, item.kind, item.target, key, item.title, item.body, timestamp(current))
    finances.budget_reminders(db, user, timestamp(current))
    library_queue.refresh_expired(db, user["university_id"])
    return len(wanted)


def refresh(db, user):
    with write(db):
        return refresh_in_transaction(db, user, now())


def refresh_all(db):
    users = list(db.execute("""
        SELECT a.*, u.timezone FROM account a JOIN university u ON u.id=a.university_id WHERE a.role='student'
    """))
    return len(users), sum(refresh(db, user) for user in users)
