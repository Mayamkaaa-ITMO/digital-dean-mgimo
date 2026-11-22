from datetime import date, datetime, timedelta
from uuid import NAMESPACE_URL, uuid5

from . import services as svc
from .applications import answers, version_check
from .domain import DomainError, field, identifier, require, role, timestamp
from .fixtures import demo_time
from .schedule import local_value, today_for
from .workspace import scope

STATES = {"active": "Практика идёт", "review": "Отчёт на проверке", "changes": "Нужна доработка",
          "passed": "Практика зачтена", "failed": "Практика не зачтена"}


def seed(db, university, anchor):
    closes = demo_time(anchor + timedelta(days=21), 18)
    for code, days in (("intern-data", 14), ("energy-team", 21)):
        oid = uuid5(NAMESPACE_URL, f"dean:{university}:opportunity:{code}").hex
        db.execute("INSERT INTO practice_offer VALUES (?,?,?,?) ON CONFLICT DO NOTHING",
                   (oid, university, days, closes))


def offer(db, user, oid):
    university, _ = scope(user)
    return db.execute("SELECT * FROM practice_offer WHERE university_id=? AND id=?", (university, oid)).fetchone()


def cases(db, user, pid=None, *, manage=False):
    query = """
        SELECT c.*,p.title,a.full_name AS student,u.full_name AS supervisor
        FROM practice_case c JOIN opportunity p ON p.id=c.offer_id AND p.university_id=c.university_id
        JOIN account a ON a.id=c.student_id AND a.university_id=c.university_id
        JOIN account u ON u.id=c.supervisor_id AND u.university_id=c.university_id
        WHERE c.university_id=?
    """
    if manage:
        role(user, "staff")
        query += """ AND c.supervisor_id=? AND EXISTS (
            SELECT 1 FROM staff_group g WHERE g.university_id=c.university_id AND g.group_id=c.group_id
            AND g.staff_id=c.supervisor_id)"""
        args = (user["university_id"], user["id"])
    else:
        query += " AND c.student_id=?"
        args = scope(user)
    if pid:
        return require(db.execute(query + " AND c.id=?", (*args, pid)).fetchone())
    return list(db.execute(query + " ORDER BY c.starts_at DESC,c.rowid DESC", args))


def record(db, user, pid, state, note, now):
    db.execute("""
        INSERT INTO practice_history (case_id,actor_id,state,note,created_at) VALUES (?,?,?,?,?)
    """, (pid, user["id"], state, note, now))


def approve(db, user, application, now):
    selected = answers(application).get("_links", {}).get("practice", {}).get("id")
    supervisor = application["assignee_id"]
    if supervisor != user["id"]:
        raise DomainError(403, "Решение принимает назначенный руководитель.")
    row = require(db.execute("""
        SELECT f.*,o.title FROM practice_offer f JOIN opportunity o ON o.id=f.id AND o.university_id=f.university_id
        WHERE f.university_id=? AND f.id=?
    """, (application["university_id"], selected)).fetchone())
    if db.execute("""
        SELECT 1 FROM practice_case WHERE university_id=? AND student_id=? AND offer_id=?
    """, (row["university_id"], application["student_id"], selected)).fetchone():
        raise DomainError(409, "Для этого студента практика по предложению уже создана.")
    pid = identifier()
    due = timestamp(datetime.fromisoformat(now) + timedelta(days=row["duration_days"]))
    db.execute("""
        INSERT INTO practice_case (id,university_id,student_id,group_id,offer_id,application_id,supervisor_id,state,starts_at,due_at)
        VALUES (?,?,?,?,?,?,?,'active',?,?)
    """, (pid, row["university_id"], application["student_id"], application["group_id"], selected,
          application["id"], user["id"], now, due))
    record(db, user, pid, "active", "Назначен руководитель и установлен срок отчёта.", now)
    svc.notify(db, row["university_id"], application["student_id"], "practice", pid, f"practice:{pid}:assigned",
               "Практика назначена", row["title"], now)


def diary(db, user, pid, raw_day, body, key):
    note = field(body, "Запись дневника", 1800, 5)
    try:
        day = date.fromisoformat(raw_day)
    except ValueError:
        raise DomainError(422, "Укажи дату работы.") from None

    def apply():
        row = cases(db, user, pid)
        if row["state"] not in {"active", "changes"}:
            raise DomainError(409, "Дневник доступен во время работы и доработки.")
        if not local_value(row["starts_at"], user["timezone"]).date() <= day <= today_for(user):
            raise DomainError(422, "Укажи прошедшую или сегодняшнюю дату в периоде практики.")
        entry_id = identifier()
        changed = db.execute("""
            INSERT INTO practice_diary VALUES (?,?,?,?,?) ON CONFLICT(case_id,day) DO NOTHING
        """, (entry_id, pid, day.isoformat(), note, timestamp())).rowcount
        if not changed:
            raise DomainError(409, "За этот день запись уже есть.")
        return entry_id

    return svc.command(db, user, "practice.diary", key, [pid, day.isoformat(), note], apply)


def submit_report(db, user, pid, body, version, key):
    note = field(body, "Отчёт", 2500, 10)

    def apply():
        row = cases(db, user, pid)
        version_check(row, version)
        if row["state"] not in {"active", "changes"}:
            raise DomainError(409, "Сейчас новая версия отчёта недоступна.")
        if not db.execute("SELECT 1 FROM practice_diary WHERE case_id=?", (pid,)).fetchone():
            raise DomainError(422, "Сначала добавь хотя бы одну запись в дневник.")
        revision = db.execute("SELECT count(*)+1 FROM practice_report WHERE case_id=?", (pid,)).fetchone()[0]
        now = timestamp()
        db.execute("INSERT INTO practice_report VALUES (?,?,?,?,?)", (identifier(), pid, revision, note, now))
        db.execute("UPDATE practice_case SET state='review',version=version+1 WHERE id=?", (pid,))
        record(db, user, pid, "review", f"Отчёт версии {revision} отправлен.", now)
        svc.notify(db, row["university_id"], user["id"], "practice", pid, f"practice:{pid}:report:{revision}",
                   "Отчёт отправлен руководителю", row["title"], now)
        return pid
    return svc.command(db, user, "practice.submit", key, [pid, note, version], apply)


def review(db, user, pid, state, feedback, version, key):
    if state not in {"changes", "passed", "failed"}:
        raise DomainError(422, "Выбери решение по отчёту.")
    note = field(feedback, "Комментарий руководителя", 600, 3)

    def apply():
        row = cases(db, user, pid, manage=True)
        version_check(row, version)
        if row["state"] != "review":
            raise DomainError(409, "Решение доступно после отправки отчёта.")
        now = timestamp()
        db.execute("UPDATE practice_case SET state=?,version=version+1 WHERE id=?", (state, pid))
        record(db, user, pid, state, note, now)
        svc.notify(db, row["university_id"], row["student_id"], "practice", pid,
                   f'practice:{pid}:{row["version"]}', STATES[state], row["title"], now)
        return pid
    return svc.command(db, user, "practice.review", key, [pid, state, note, version], apply)
