from datetime import timedelta
from uuid import NAMESPACE_URL, uuid5

from . import services as svc
from .domain import DomainError, require, timestamp
from .schedule import local_midnight, local_value
from .workspace import scope


def stable_id(university, value):
    return uuid5(NAMESPACE_URL, f"dean:{university}:curriculum:{value}").hex


def seed_curriculum(db, university):
    rows = list(db.execute("""
        SELECT DISTINCT g.program_id,r.period_id,r.subject_id,s.name FROM learning_result r
        JOIN account a ON a.id=r.student_id AND a.university_id=r.university_id
        JOIN study_group g ON g.id=a.group_id AND g.university_id=a.university_id
        JOIN subject s ON s.id=r.subject_id AND s.university_id=r.university_id WHERE r.university_id=?
    """, (university,)))
    for row in rows:
        group = (row["program_id"], row["period_id"], row["subject_id"])
        db.execute("""
            INSERT INTO curriculum_item (id,university_id,program_id,period_id,subject_id,title,credits,kind)
            VALUES (?, ?, ?, ?, ?, ?, ?, 'required') ON CONFLICT(id) DO NOTHING
        """, (stable_id(university, ":".join(group)), university, *group, row["name"],
              3 if row["subject_id"] in {"english", "graphics"} else 5))
    demo = db.execute("""
        SELECT min(e.starts_at) AS anchor,u.timezone FROM schedule_event e
        JOIN university u ON u.id=e.university_id WHERE e.university_id=? GROUP BY u.timezone
    """, (university,)).fetchone()
    if not demo:
        return
    zone = demo["timezone"]
    date = local_value(demo["anchor"], zone).date()
    begins = local_midnight(date - timedelta(days=2), zone)
    ends = local_midnight(date + timedelta(days=16), zone)
    for program, slug, name, capacity in (
        ("ai", "data", "Введение в анализ данных", 1),
        ("ai", "projects", "Проектный практикум", 4),
        ("energy", "model", "Моделирование электрических систем", 4),
    ):
        semester = db.execute("""
            SELECT DISTINCT a.period_id FROM account a JOIN study_group g
            ON g.university_id=a.university_id AND g.id=a.group_id
            WHERE a.university_id=? AND g.program_id=? AND a.role='student' ORDER BY a.period_id DESC LIMIT 1
        """, (university, program)).fetchone()
        if semester is None:
            continue
        pid = semester["period_id"]
        db.execute("""
            INSERT INTO curriculum_item
              (id,university_id,program_id,period_id,title,credits,kind,window_start,window_end,capacity)
            VALUES (?, ?, ?, ?, ?, 3, 'elective', ?, ?, ?) ON CONFLICT(id) DO NOTHING
        """, (stable_id(university, f"{program}:{pid}:{slug}"), university, program, pid, name, begins, ends, capacity))


def items(db, user):
    university, actor = scope(user)
    return list(db.execute("""
        SELECT c.*, p.label AS period, r.id AS result_id, r.outcome, r.score,
            COALESCE(choice.item_id=c.id,0) AS chosen,
            (SELECT count(*) FROM curriculum_choice ch WHERE ch.university_id=c.university_id AND ch.item_id=c.id) AS taken
        FROM curriculum_item c
        JOIN study_group g ON g.university_id=c.university_id AND g.program_id=c.program_id AND g.id=?
        JOIN academic_period p ON p.university_id=c.university_id AND p.id=c.period_id
        LEFT JOIN learning_result r ON r.university_id=c.university_id AND r.period_id=c.period_id
            AND r.student_id=? AND r.subject_id=c.subject_id
        LEFT JOIN curriculum_choice choice ON choice.university_id=c.university_id AND choice.period_id=c.period_id AND choice.student_id=?
        WHERE c.university_id=? AND NOT EXISTS (
            SELECT 1 FROM curriculum_block_item m JOIN curriculum_block b ON b.id=m.block_id
            WHERE m.item_id=c.id AND b.rule='credits')
        ORDER BY p.starts_on DESC,c.kind DESC,c.title
    """, (user["group_id"], actor, actor, university)))


def choose(db, user, item_id, key):
    scope(user)

    def apply():
        record = require(next((row for row in items(db, user) if row["id"] == item_id), None))
        if record["kind"] != "elective" or record["period_id"] != user["period_id"]:
            raise DomainError(422, "Выбери факультатив текущего семестра.")
        current = timestamp()
        if not record["window_start"] <= current < record["window_end"]:
            raise DomainError(409, "Окно выбора сейчас закрыто.")
        if record["chosen"]:
            return item_id
        if record["taken"] >= record["capacity"]:
            raise DomainError(409, "Свободных мест больше нет. Выбери другой факультатив.")
        db.execute("""
            INSERT INTO curriculum_choice VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(university_id,student_id,period_id) DO UPDATE SET item_id=excluded.item_id,selected_at=excluded.selected_at
        """, (*scope(user), record["period_id"], item_id, current))
        return item_id

    return svc.command(db, user, "curriculum.choose", key, [item_id], apply)
