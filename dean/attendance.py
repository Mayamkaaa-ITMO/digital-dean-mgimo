from . import services as svc
from .domain import DomainError, field, require, timestamp
from .schedule import staff_lessons

STATES = {"present": "Присутствовал", "absent": "Отсутствовал", "excused": "Уважительная причина"}


def roster(db, user, eid):
    lesson = staff_lessons(db, user, eid)
    return list(db.execute("""
        SELECT a.id,a.full_name,r.state FROM account a LEFT JOIN lesson_attendance r ON r.student_id=a.id AND r.event_id=?
        WHERE a.university_id=? AND a.group_id=? AND a.period_id=? AND a.role='student'
        ORDER BY a.full_name
    """, (eid, user["university_id"], lesson["group_id"], lesson["period_id"])))


def own(db, user, eid):
    return db.execute("SELECT state FROM lesson_attendance WHERE event_id=? AND student_id=?", (eid, user["id"])).fetchone()


def record(db, user, eid, student, state, reason, key):
    if state not in STATES:
        raise DomainError(422, "Выбери отметку посещения.")
    note = field(reason, "Причина отметки", 300, 0)

    def apply():
        lesson = staff_lessons(db, user, eid)
        if lesson["starts_at"] > timestamp():
            raise DomainError(409, "Занятие ещё не началось.")
        row = require(next((r for r in roster(db, user, eid) if r["id"] == student), None))
        if row["state"] == state:
            return eid
        if row["state"] and not note:
            raise DomainError(422, "Для исправления укажи причину.")
        now = timestamp()
        db.execute("""
            INSERT INTO lesson_attendance VALUES (?,?,?) ON CONFLICT(event_id,student_id)
            DO UPDATE SET state=excluded.state
        """, (eid, student, state))
        event = db.execute("""
            INSERT INTO lesson_attendance_history (event_id,student_id,actor_id,state,reason,changed_at)
            VALUES (?,?,?,?,?,?)
        """, (eid, student, user["id"], state, note, now)).lastrowid
        svc.notify(db, user["university_id"], student, "schedule", eid, f"lesson-attendance:{event}",
                   "Посещаемость обновлена", STATES[state], now)
        return eid

    return svc.command(db, user, "attendance.record", key, [eid, student, state, note], apply)
