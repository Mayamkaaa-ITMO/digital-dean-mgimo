"""Private subject records, with an optional explicit public-syllabus reference."""

from . import learning, public_schedule, workspace
from .clock import now
from .domain import DomainError, field, identifier, require, role, timestamp
from .services import command


def subjects(db, user):
    role(user, "student")
    return list(db.execute("SELECT * FROM personal_subject WHERE owner_id=? ORDER BY title,id", (user["id"],)))


def subject(db, user, sid):
    role(user, "student")
    return require(db.execute("SELECT * FROM personal_subject WHERE id=? AND owner_id=?", (sid, user["id"])).fetchone())


def period(selection):
    source = public_schedule.SOURCES[selection[0]]
    return " · ".join(filter(None, (source["academic_year"], source["term"])))


def suggestions(db, selection):
    if not selection:
        return []
    from_schedule = db.execute("""
        SELECT DISTINCT subject FROM public_lesson WHERE source_id=? AND group_id=? AND cancelled=0
    """, selection)
    group = public_schedule.SOURCES[selection[0]]["groups"][selection[1]]
    return sorted({row[0] for row in from_schedule} | {row["subject"] for row in group.get("pattern", ())},
                  key=str.casefold)


def related_lessons(db, row):
    selection = (row["source_id"], row["group_id"])
    if row["source_id"] not in public_schedule.SOURCES or row["period"] != period(selection):
        return []
    return list(db.execute("""
        SELECT id,subject,kind,starts_at,ends_at,cancelled FROM public_lesson
        WHERE source_id=? AND group_id=? AND subject=? AND cancelled=0 AND starts_at>=?
        ORDER BY starts_at,id LIMIT 6
    """, (row["source_id"], row["group_id"], row["source_title"], timestamp(now()))))


def linked_tasks(db, user, sid):
    subject(db, user, sid)
    return list(db.execute("""
        SELECT t.* FROM personal_subject_task l JOIN task t ON t.id=l.task_id
        WHERE l.subject_id=? AND t.owner_id=? AND t.university_id=?
        AND NOT EXISTS (SELECT 1 FROM team_task g WHERE g.task_id=t.id)
        ORDER BY t.state='todo' DESC,t.due_at IS NULL,t.due_at,t.id
    """, (sid, user["id"], user["university_id"])))


def origin(db, user, selection, source_title):
    if not selection or public_schedule.context(db, user) != selection or source_title not in suggestions(db, selection):
        raise DomainError(409, "Предмета больше нет в выбранном расписании. Обнови страницу.")
    return (*selection, period(selection), source_title)


def create(db, user, title, selection, source_title, key):
    role(user, "student")
    title = field(source_title or title, "Название предмета", 160)

    def apply():
        link = origin(db, user, selection, source_title) if source_title else (None,) * 4
        if source_title:
            existing = db.execute("""
                SELECT id FROM personal_subject WHERE owner_id=? AND source_id=? AND group_id=? AND period=? AND source_title=?
            """, (user["id"], *link)).fetchone()
            if existing:
                return existing["id"]
        sid = identifier()
        db.execute("""
            INSERT INTO personal_subject(id,owner_id,title,source_id,group_id,period,source_title) VALUES (?,?,?,?,?,?,?)
        """, (sid, user["id"], title, *link))
        return sid

    return command(db, user, "subject.create", key, [title, selection, source_title], apply)


def result(db, sid):
    row = db.execute("SELECT * FROM personal_subject_result WHERE subject_id=?", (sid,)).fetchone()
    if row is None:
        return None, None
    weight = row["exam_weight"] * 100
    components = [
        {"kind": "coursework", "title": "Работы", "weight_bp": 10000-weight, "max_points": 100,
         "points": row["coursework"] if weight < 10000 else 0},
        {"kind": "exam", "title": "Экзамен", "weight_bp": weight, "max_points": 100, "points": None},
    ]
    return row, learning.scores(components, row["target"], row["exam_points"])


def edit(db, user, sid, action, inputs, selection, key):
    role(user, "student")
    allowed = {"save", "link", "unlink", "task", "detach", "result"}
    if action not in allowed:
        raise DomainError(422, "Действие с предметом недоступно.")

    def apply():
        row = subject(db, user, sid)
        if str(row["revision"]) != inputs.get("revision"):
            raise DomainError(409, "Предмет изменился. Открой его заново.")
        if action == "save":
            title = field(inputs.get("title", ""), "Предмет", 160)
            note = field(inputs.get("note", ""), "Заметка", 1600, 0)
            db.execute("UPDATE personal_subject SET title=?,note=? WHERE id=?", (title, note, sid))
        elif action in {"link", "unlink"}:
            values = origin(db, user, selection, inputs.get("source_title")) if action == "link" else (None,) * 4
            existing = db.execute("""
                SELECT id FROM personal_subject WHERE owner_id=? AND source_id=? AND group_id=? AND period=?
                AND source_title=? AND id<>?
            """, (user["id"], *values, sid)).fetchone()
            if existing:
                raise DomainError(409, "У этой дисциплины уже есть личная карточка.")
            db.execute("UPDATE personal_subject SET source_id=?,group_id=?,period=?,source_title=? WHERE id=?",
                       (*values, sid))
        elif action in {"task", "detach"}:
            task_id = workspace.task(db, user, inputs.get("task_id"))["id"]
            if action == "task":
                previous = db.execute("SELECT subject_id FROM personal_subject_task WHERE task_id=?", (task_id,)).fetchone()
                if previous and previous["subject_id"] != sid:
                    raise DomainError(409, "Задача уже связана с другим личным предметом.")
                db.execute("INSERT OR IGNORE INTO personal_subject_task VALUES (?,?)", (task_id, sid))
            else:
                db.execute("DELETE FROM personal_subject_task WHERE task_id=? AND subject_id=?", (task_id, sid))
        else:
            weight = learning.optional_int(inputs.get("weight"), "Вес экзамена", 100)
            if not weight:
                raise DomainError(422, "Укажи вес экзамена от 1 до 100.")
            values = [learning.optional_int(inputs.get(name), label, 100) for name, label in
                      (("coursework", "Работы"), ("exam_points", "Экзамен"), ("target", "Цель"))]
            cw, exam, target = values
            db.execute("""
                INSERT INTO personal_subject_result VALUES (?,?,?,?,?)
                ON CONFLICT(subject_id) DO UPDATE SET coursework=excluded.coursework,
                    exam_points=excluded.exam_points, exam_weight=excluded.exam_weight, target=excluded.target
            """, (sid, cw, exam, weight, target))
        db.execute("UPDATE personal_subject SET revision=revision+1 WHERE id=?", (sid,))
        return sid

    return command(db, user, "subject." + action, key, [sid, inputs, selection], apply)
