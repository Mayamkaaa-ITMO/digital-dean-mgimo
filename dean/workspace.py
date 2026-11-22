from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from . import services as svc
from .applications import version_check
from .clock import now
from .domain import DomainError, field, identifier, require, role, timestamp
from .schedule import SQLiteScheduleProvider, edit_time, local_midnight, local_value, student_scope

PRIORITIES = {"low": "Низкий", "normal": "Обычный", "high": "Высокий"}
NOTE_TARGETS = {"subject": "subject_id", "lesson": "lesson_id", "task": "task_id"}
MAX_OCCURRENCES = 26


def scope(user):
    role(user, "student")
    return user["university_id"], user["id"]


def subjects(db, user):
    return list(db.execute("""
        SELECT DISTINCT s.id, s.name FROM subject s JOIN learning_result r
        ON s.university_id = r.university_id AND s.id = r.subject_id
        WHERE r.university_id = ? AND r.student_id = ? ORDER BY s.name
    """, scope(user)))


def tasks(db, user, *, state="", priority="", subject="", text=""):
    query = """
        SELECT t.*, s.name AS subject, (SELECT count(*) FROM subtask u WHERE u.task_id = t.id) AS total,
               (SELECT count(*) FROM subtask u WHERE u.task_id = t.id AND u.done = 1) AS done
        FROM task t LEFT JOIN subject s ON s.university_id = t.university_id AND s.id = t.subject_id
        WHERE t.university_id = ? AND t.owner_id = ? AND NOT EXISTS (
            SELECT 1 FROM team_task shared WHERE shared.task_id=t.id)
    """
    params = scope(user)
    for column, value in (("state", state), ("priority", priority), ("subject_id", subject)):
        if value:
            query += f" AND t.{column} = ?"
            params += (value,)
    query += " ORDER BY t.state DESC, t.due_at IS NULL, t.due_at, t.created_at DESC, t.rowid DESC"
    term = text.strip().casefold()[:120]
    return [row for row in db.execute(query, params)
            if term in (row["title"] + " " + row["description"]).casefold()]


def task(db, user, tid):
    return require(db.execute("""
        SELECT t.*, s.name AS subject, p.result_id AS exam_result,
               (SELECT w.id FROM coursework_student w WHERE w.task_id=t.id AND w.student_id=t.owner_id) AS coursework_id,
               (SELECT l.lecture_id FROM lecture_homework_task l WHERE l.task_id=t.id AND l.owner_id=t.owner_id) AS lecture_id
        FROM task t LEFT JOIN subject s ON s.university_id = t.university_id AND s.id = t.subject_id
        LEFT JOIN preparation_plan p ON p.id = t.plan_id
        WHERE t.university_id = ? AND t.owner_id = ? AND t.id = ? AND NOT EXISTS (
            SELECT 1 FROM team_task shared WHERE shared.task_id=t.id)
    """, (*scope(user), tid)).fetchone())


def task_values(db, user, inputs):
    title = field(inputs.get("title", ""), "Название", 120)
    description = field(inputs.get("description", ""), "Описание", 1600, 0)
    subject = inputs.get("subject", "") or None
    if subject is not None and subject not in {row["id"] for row in subjects(db, user)}:
        raise DomainError(422, "Выбери дисциплину из своего учебного списка.")
    due = inputs.get("due", "")
    due = timestamp(edit_time(due, user["timezone"])) if due else None
    priority = inputs.get("priority", "normal")
    if priority not in PRIORITIES:
        raise DomainError(422, "Выбери допустимый приоритет.")
    return title, description, subject, due, priority


def add_task(db, user, values, *, plan=None):
    tid, current = identifier(), timestamp()
    db.execute("""
        INSERT INTO task (id, university_id, owner_id, title, description, subject_id, due_at, priority,
                          created_at, updated_at, plan_id)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (tid, *scope(user), *values, current, current, plan))
    return tid


def save_task(db, user, inputs, key, tid=None):
    values = task_values(db, user, inputs)
    if tid is not None:
        task(db, user, tid)

    def apply():
        if tid is None:
            return add_task(db, user, values)
        version_check(task(db, user, tid), inputs.get("version"))
        db.execute("""
            UPDATE task SET title=?, description=?, subject_id=?, due_at=?, priority=?,
                            updated_at=?, version=version+1 WHERE id=?
        """, (*values, timestamp(), tid))
        return tid

    return svc.command(db, user, "task.save", key, [tid, values], apply)


def task_action(db, user, tid, action, version, key, *, complete_subtasks=False):
    def apply():
        row = task(db, user, tid)
        version_check(row, version)
        if action == "delete":
            db.execute("DELETE FROM task WHERE id=?", (tid,))
        else:
            if row["coursework_id"]:
                raise DomainError(422, "Статус этой задачи меняется при сдаче работы или возврате на доработку.")
            if action == "complete":
                if complete_subtasks:
                    db.execute("UPDATE subtask SET done=1 WHERE task_id=? AND done=0", (tid,))
                elif db.execute("SELECT 1 FROM subtask WHERE task_id=? AND done=0 LIMIT 1", (tid,)).fetchone():
                    raise DomainError(422, "Заверши открытые подзадачи или отметь, что их можно завершить вместе с задачей.")
            state = "done" if action == "complete" else "todo"
            db.execute("UPDATE task SET state=?, updated_at=?, version=version+1 WHERE id=?", (state, timestamp(), tid))
        return tid

    payload = [tid, action, True] if complete_subtasks else [tid, action]
    return svc.command(db, user, "task.action", key, payload, apply)


def subtasks(db, user, tid):
    task(db, user, tid)
    return list(db.execute("SELECT * FROM subtask WHERE task_id=? ORDER BY rowid", (tid,)))


def subtask_action(db, user, tid, action, sid, title, key, *, authorize=None):
    title = field(title, "Название подзадачи", 120) if action == "add" else ""

    def apply():
        if authorize:
            authorize()
        else:
            parent = task(db, user, tid)
            if action in {"add", "reopen"} and parent["state"] == "done" and not parent["coursework_id"]:
                db.execute("UPDATE task SET state='todo', updated_at=?, version=version+1 WHERE id=?",
                           (timestamp(), tid))
        if action == "add":
            new_id = identifier()
            db.execute("INSERT INTO subtask (id, task_id, title) VALUES (?, ?, ?)", (new_id, tid, title))
            return new_id
        row = require(db.execute("SELECT id FROM subtask WHERE id=? AND task_id=?", (sid, tid)).fetchone())
        if action == "delete":
            db.execute("DELETE FROM subtask WHERE id=?", (row["id"],))
        else:
            db.execute("UPDATE subtask SET done=? WHERE id=?", (action == "complete", row["id"]))
        return row["id"]

    return svc.command(db, user, "subtask.action", key, [tid, action, sid, title], apply)


def note_target(db, user, kind, target):
    if kind == "task":
        return task(db, user, target)["title"], f"/tasks/{target}"
    if kind == "lesson":
        lesson = require(SQLiteScheduleProvider(db).occurrence(student_scope(user), target))
        return lesson.subject, f"/schedule/{target}"
    result = next((row for row in svc.results(db, user) if row["subject_id"] == target), None)
    if result:
        return result["subject"], f"/grades/{result['id']}"
    for row in subjects(db, user):
        if row["id"] == target:
            return row["name"], "/grades"
    raise DomainError(404, "Дисциплина недоступна.")


def notes(db, user, kind, target):
    note_target(db, user, kind, target)
    column = NOTE_TARGETS[kind]
    return list(db.execute(f"""
        SELECT * FROM note WHERE university_id=? AND owner_id=? AND {column}=? ORDER BY rowid DESC
    """, (*scope(user), target)))


def note(db, user, nid):
    row = require(db.execute("SELECT * FROM note WHERE university_id=? AND owner_id=? AND id=?",
                             (*scope(user), nid)).fetchone())
    kind, target = next((key, row[column]) for key, column in NOTE_TARGETS.items() if row[column] is not None)
    _, href = note_target(db, user, kind, target)
    return row, href


def write_note(db, user, key, body="", *, kind=None, target=None, nid=None, delete=False):
    values = field(body, "Заметка", 1600) if not delete else ""

    def apply():
        if nid is None:
            note_target(db, user, kind, target)
            new_id, stamp = identifier(), timestamp()
            column = NOTE_TARGETS[kind]
            db.execute(f"""
                INSERT INTO note (id, university_id, owner_id, {column}, body, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (new_id, *scope(user), target, values, stamp, stamp))
            return new_id
        record, href = note(db, user, nid)
        if delete:
            db.execute("DELETE FROM note WHERE id=?", (record["id"],))
        else:
            db.execute("UPDATE note SET body=?, updated_at=? WHERE id=?", (values, timestamp(), record["id"]))
        return href

    return svc.command(db, user, "note.write", key, [kind, target, nid, values, delete], apply)


def plan_for(db, user, result_id):
    return db.execute("""
        SELECT p.*, r.exam_at AS current_exam_at FROM preparation_plan p
        JOIN learning_result r ON r.id=p.result_id
        WHERE p.university_id=? AND p.owner_id=? AND p.result_id=?
    """, (*scope(user), result_id)).fetchone()


def create_plan(db, user, rid, key):
    def apply():
        result = svc.results(db, user, result_id=rid)
        existing = plan_for(db, user, rid)
        if existing:
            return existing["id"]
        clock = now()
        exam = datetime.fromisoformat(result["exam_at"]) if result["exam_at"] else None
        if exam is None or exam <= clock:
            raise DomainError(422, "План доступен для экзамена в будущем.")
        plan = identifier()
        db.execute("INSERT INTO preparation_plan VALUES (?, ?, ?, ?, ?, ?)",
                   (plan, *scope(user), rid, result["exam_at"], timestamp(clock)))
        titles = ("Собрать темы и материалы", "Решить тренировочные задания", "Проверить пробелы перед экзаменом")
        for number, title in enumerate(titles, 1):
            due = timestamp(clock + (exam - clock) * number / 4)
            add_task(db, user, (title, "", result["subject_id"], due, "normal"), plan=plan)
        return plan

    return svc.command(db, user, "study.plan", key, [rid], apply)


def parse_date(raw):
    try:
        day = date.fromisoformat(raw)
        if day.isoformat() == raw and 1900 <= day.year <= 9900:
            return day
    except ValueError:
        pass
    raise DomainError(422, "Укажи корректную дату.")


def event(db, user, eid):
    return require(db.execute("SELECT * FROM personal_event WHERE university_id=? AND owner_id=? AND id=?",
                              (*scope(user), eid)).fetchone())


def event_values(user, inputs):
    title = field(inputs.get("title", ""), "Название", 120)
    info = field(inputs.get("description", ""), "Описание", 1600, 0)
    location = field(inputs.get("location", ""), "Место", 120, 0)
    zone = user["timezone"]
    all_day = inputs.get("all_day") == "1"
    if all_day:
        first = parse_date(inputs.get("first_day", ""))
        last = parse_date(inputs.get("last_day", ""))
        if last < first:
            raise DomainError(422, "Последний день должен быть не раньше первого.")
        start, end = local_midnight(first, zone), local_midnight(last + timedelta(days=1), zone)
    else:
        begin = edit_time(inputs.get("starts", ""), zone)
        finish = edit_time(inputs.get("ends", ""), zone)
        if finish <= begin:
            raise DomainError(422, "Окончание должно быть позже начала.")
        start, end = timestamp(begin), timestamp(finish)
        first = local_value(start, zone).date()
    repeat = inputs.get("repeat_until", "")
    if repeat:
        until = parse_date(repeat)
        if until < first or (until - first).days // 7 >= MAX_OCCURRENCES:
            raise DomainError(422, f"Укажи дату окончания повтора. Допустимо до {MAX_OCCURRENCES} еженедельных событий.")
    return title, info, location, start, end, int(all_day), repeat or None


def occurrences(row, zone):
    starts = local_value(row["starts_at"], zone)
    ends = local_value(row["ends_at"], zone)
    last = date.fromisoformat(row["repeat_until"]) if row["repeat_until"] else starts.date()
    for index in range(min(MAX_OCCURRENCES, (last - starts.date()).days // 7 + 1)):
        offset = timedelta(weeks=index)
        yield (starts.date() + offset).isoformat(), timestamp(starts + offset), timestamp(ends + offset)


def add_event(db, user, values):
    new_id = identifier()
    db.execute("""
        INSERT INTO personal_event (id, university_id, owner_id, title, description, location,
                                    starts_at, ends_at, all_day, repeat_until)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (new_id, *scope(user), *values))
    return new_id


def save_event(db, user, inputs, key, eid=None):
    values = event_values(user, inputs)
    if eid is not None:
        event(db, user, eid)

    def apply():
        if eid is None:
            return add_event(db, user, values)
        version_check(event(db, user, eid), inputs.get("version"))
        db.execute("""
            UPDATE personal_event SET title=?, description=?, location=?, starts_at=?, ends_at=?,
                                      all_day=?, repeat_until=?, version=version+1 WHERE id=?
        """, (*values, eid))
        valid_days = {day for day, _, _ in occurrences(event(db, user, eid), user["timezone"])}
        skips = list(db.execute("SELECT occurrence_day FROM personal_event_skip WHERE event_id=?", (eid,)))
        db.executemany("DELETE FROM personal_event_skip WHERE event_id=? AND occurrence_day=?",
                       [(eid, row[0]) for row in skips if row[0] not in valid_days])
        return eid

    return svc.command(db, user, "event.save", key, [eid, values], apply)


def event_action(db, user, eid, operation, day, version, key):
    def apply():
        row = event(db, user, eid)
        version_check(row, version)
        if operation == "delete":
            from .calendar_export import record_cancelled

            record_cancelled(db, user, row)
            db.execute("DELETE FROM personal_event WHERE id=?", (eid,))
        else:
            if not row["repeat_until"] or day not in {d for d, _, _ in occurrences(row, user["timezone"])}:
                raise DomainError(422, "Выбери существующее повторение серии.")
            db.execute("INSERT OR IGNORE INTO personal_event_skip VALUES (?, ?)", (eid, day))
            db.execute("UPDATE personal_event SET version=version+1 WHERE id=?", (eid,))
        return eid

    return svc.command(db, user, "event.action", key, [eid, operation, day], apply)


def personal_occurrences(db, user, window_start, window_end):
    rows = db.execute("""
        SELECT * FROM personal_event WHERE university_id=? AND owner_id=? AND starts_at < ?
        AND (ends_at > ? OR repeat_until IS NOT NULL)
    """, (*scope(user), window_end, window_start))
    skips = set(map(tuple, db.execute("""
        SELECT s.event_id, s.occurrence_day FROM personal_event_skip s
        JOIN personal_event e ON e.id=s.event_id WHERE e.university_id=? AND e.owner_id=?
    """, scope(user))))
    for row in rows:
        for day, begins, ends in occurrences(row, user["timezone"]):
            if begins < window_end and ends > window_start and (row["id"], day) not in skips:
                yield row, day, begins, ends
