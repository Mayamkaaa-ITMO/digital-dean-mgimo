import json
from datetime import datetime, timedelta
from fractions import Fraction

from . import services as svc
from .applications import version_check
from .clock import now
from .domain import DomainError, identifier, require, role, timestamp
from .schedule import edit_time

STATES = {
    "scheduled": "Предстоит", "awaiting": "Ожидает проверки", "graded": "Оценено",
    "failed": "Не сдано", "ungraded": "Оценка отсутствует",
}
FIXTURE = {
    ("student-a", "databases"): (
        ("lab", "Лабораторные работы", "coursework", 50, 4000, 42, "graded"),
        ("quiz", "Контрольная работа", "coursework", 25, 2000, 20, "graded"),
        ("exam", "Итоговый экзамен", "exam", 30, 4000, None, "scheduled"),
    ),
    ("student-a", "algorithms"): (
        ("practice", "Разбор задач", "coursework", 60, 6000, 52, "graded"),
        ("exam", "Итоговый экзамен", "exam", 40, 4000, 34, "graded"),
    ),
    ("student-b", "physics"): (
        ("exam", "Итоговый экзамен", "exam", 100, 10000, 0, "failed"),
    ),
    ("student-b", "circuits"): (
        ("lab", "Лабораторный отчёт", "coursework", 40, 4000, None, "awaiting"),
        ("exam", "Итоговый экзамен", "exam", 60, 6000, None, "scheduled"),
    ),
}


def seed_assessments(db, university):
    for (login, subject), fixtures in FIXTURE.items():
        row = db.execute("""
            SELECT r.id, r.exam_at, (
                SELECT min(e.starts_at) FROM schedule_event e
                WHERE e.university_id=r.university_id AND e.group_id=u.group_id AND e.period_id=r.period_id
            ) AS first_lesson FROM learning_result r
            JOIN account u ON u.id=r.student_id AND u.university_id=r.university_id
            WHERE r.university_id=? AND u.username=? AND r.subject_id=? AND r.period_id=u.period_id
        """, (university, login, subject)).fetchone()
        if row is None:
            continue
        anchor = row["exam_at"] or row["first_lesson"]
        due = timestamp(datetime.fromisoformat(anchor) - timedelta(days=2)) if anchor else None
        db.executemany("""
            INSERT INTO assessment (id, result_id, code, title, kind, max_points, weight_bp, points, state, due_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(result_id, code) DO NOTHING
        """, [(identifier(), row["id"], *fixture,
               row["exam_at"] if fixture[2] == "exam" else due) for fixture in fixtures])


def assessments(db, rid):
    return list(db.execute("SELECT * FROM assessment WHERE result_id=? ORDER BY rowid", (rid,)))


def grade_progress(row, components):
    if row["outcome"] == "failed":
        return "failed"
    if row["outcome"] == "passed" or row["score"] is not None:
        return "graded"
    if row["exam_at"] and datetime.fromisoformat(row["exam_at"]) > now():
        return "scheduled"
    if any(c["state"] == "awaiting" for c in components):
        return "awaiting"
    return "ungraded"


def optional_int(value, label, maximum):
    if not value:
        return None
    if not value.isascii() or not value.isdecimal() or len(value) > 5 or int(value) > maximum:
        raise DomainError(422, f"Укажи целое число от 0 до {maximum} в поле «{label}».")
    return int(value)


def format_weight(weight_bp):
    return f"{weight_bp / 100:g}%" if weight_bp is not None else "Не задан"


def round_score(score):
    return (score.numerator * 2 + score.denominator) // (score.denominator * 2)


def scores(components, target=None, hypothetical=None):
    components = [row for row in components if row["kind"] != "coursework" or row["weight_bp"] != 0]
    exams = [row for row in components if row["kind"] == "exam"]
    if len(exams) != 1:
        return {"problem": "Нужен один экзамен с заданными параметрами."}
    exam = exams[0]
    if (any(row["max_points"] is None or row["weight_bp"] is None for row in components)
            or sum(row["weight_bp"] for row in components) != 10000 or exam["weight_bp"] <= 0):
        return {"problem": "Нужны максимумы и веса работ, суммарный вес должен быть 100%."}
    coursework = [row for row in components if row["kind"] == "coursework"]
    unknown = [row["title"] for row in coursework if row["points"] is None]
    if unknown:
        return {"problem": f"Расчёт появится после оценки работ: {'; '.join(unknown)}."}
    base = sum((Fraction(row["points"] * row["weight_bp"], row["max_points"] * 100)
                for row in coursework), Fraction())
    contribution = Fraction(exam["weight_bp"], exam["max_points"] * 100)
    final = lambda points: round_score(base + contribution * points)
    result = {"max_exam": exam["max_points"], "exam_weight": exam["weight_bp"], "best": final(exam["max_points"])}
    if target is not None:
        result["required"] = next((points for points in range(exam["max_points"] + 1) if final(points) >= target), None)
    if hypothetical is not None and hypothetical <= exam["max_points"]:
        result["forecast"] = final(hypothetical)
    return result


def scenario(db, user, rid):
    svc.results(db, user, result_id=rid)
    return db.execute("SELECT * FROM grade_scenario WHERE result_id=?", (rid,)).fetchone()


def save_scenario(db, user, rid, goal, exam_points, key):
    svc.results(db, user, result_id=rid)
    components = assessments(db, rid)
    exam = next((row for row in components if row["kind"] == "exam"), None)
    if exam is None or exam["max_points"] is None:
        raise DomainError(422, "Для такого расчёта нужен максимум экзамена.")
    target = optional_int(goal, "Целевой итог", 100)
    hypothetical = optional_int(exam_points, "Баллы экзамена", exam["max_points"])

    def apply():
        db.execute("""
            INSERT INTO grade_scenario VALUES (?, ?, ?)
            ON CONFLICT(result_id) DO UPDATE SET target=excluded.target, exam_points=excluded.exam_points
        """, (rid, target, hypothetical))
        return rid

    return svc.command(db, user, "grade.scenario", key, [rid, target, hypothetical], apply)


def staff_results(db, user, rid=None):
    role(user, "staff")
    query = """
        SELECT r.*, s.name AS subject, u.full_name AS student_name, g.name AS group_name
        FROM learning_result r JOIN account u ON u.id=r.student_id AND u.university_id=r.university_id
        JOIN subject s ON s.id=r.subject_id AND s.university_id=r.university_id
        JOIN study_group g ON g.id=u.group_id AND g.university_id=r.university_id
        JOIN staff_group a ON a.university_id=r.university_id AND a.group_id=u.group_id
        WHERE r.university_id=? AND a.staff_id=?
    """
    params = user["university_id"], user["id"]
    if rid is not None:
        return require(db.execute(query + " AND r.id=?", (*params, rid)).fetchone())
    return list(db.execute(query + " ORDER BY u.full_name, r.period_id DESC, s.name", params))


def revisions(db, rid):
    return list(db.execute("""
        SELECT h.*, u.full_name AS actor FROM grade_revision h
        JOIN account u ON u.id=h.actor_id WHERE h.result_id=? ORDER BY h.version DESC
    """, (rid,)))


def change_grade(db, user, rid, action, fields, key):
    current = staff_results(db, user, rid)
    component = fields.get("component", "")
    if action == "component":
        record = require(db.execute("SELECT * FROM assessment WHERE result_id=? AND id=?",
                                    (rid, component)).fetchone())
        state = fields.get("state")
        if state not in STATES:
            raise DomainError(422, "Выбери статус работы.")
        if state in {"graded", "failed"} and record["max_points"] is None:
            raise DomainError(422, "Максимум работы должен быть задан.")
        points = optional_int(fields.get("points", ""), "Баллы", record["max_points"] or 0)
        if (state in {"graded", "failed"}) != (points is not None):
            raise DomainError(422, "Укажи баллы только для оценённой или несданной работы.")
        values = {"points": points, "state": state}
    else:
        if current["kind"] == "credit":
            outcome = fields.get("outcome") or None
            if outcome not in {None, "passed", "failed"}:
                raise DomainError(422, "Выбери исход зачёта.")
            values = {"score": None, "outcome": outcome, "exam_at": None}
        else:
            score = optional_int(fields.get("score", ""), "Итог", 100)
            date_time = fields.get("exam_at")
            exam_at = timestamp(edit_time(date_time, user["timezone"])) if date_time else None
            outcome = None if score is None else "passed" if score >= 60 else "failed"
            values = {"score": score, "outcome": outcome, "exam_at": exam_at}

    def apply():
        live = staff_results(db, user, rid)
        version_check(live, fields.get("version"))
        if action == "component":
            previous = require(db.execute("SELECT * FROM assessment WHERE result_id=? AND id=?",
                                          (rid, component)).fetchone())
            if db.execute("SELECT 1 FROM coursework_student WHERE assessment_id=?", (component,)).fetchone():
                raise DomainError(403, "Этот компонент оценивается в проверке учебной работы.")
            old = {key: previous[key] for key in values}
            if old == values:
                return rid
            db.execute("UPDATE assessment SET points=?, state=? WHERE id=?", (values["points"], values["state"], component))
            name = previous["title"]
            db.execute("UPDATE learning_result SET version=version+1 WHERE id=?", (rid,))
        else:
            old = {key: live[key] for key in values}
            if old == values:
                return rid
            db.execute("""
                UPDATE learning_result SET score=?, outcome=?, exam_at=?, version=version+1 WHERE id=?
            """, (*values.values(), rid))
            db.execute("UPDATE assessment SET due_at=? WHERE result_id=? AND kind='exam'", (values["exam_at"], rid))
            name = "Ведомость"
        stamp = timestamp()
        version = live["version"] + 1
        before = json.dumps({"record": name, **old}, ensure_ascii=False)
        after = json.dumps({"record": name, **values}, ensure_ascii=False)
        db.execute("""
            INSERT INTO grade_revision (result_id, actor_id, version, old_json, new_json, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (rid, user["id"], version, before, after, stamp))
        title = "Изменилась дата экзамена" if "exam_at" in old and old["exam_at"] != values["exam_at"] else "Обновлена успеваемость"
        svc.notify(db, live["university_id"], live["student_id"], "grade", rid,
                   f"grade:{rid}:{version}", title, live["subject"], stamp)
        return rid

    return svc.command(db, user, "grade.change", key, [rid, action, component, values], apply)
