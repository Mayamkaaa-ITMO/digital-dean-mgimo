import json
import os
from datetime import date, datetime, time, timedelta
from pathlib import Path
from tempfile import NamedTemporaryFile
from uuid import NAMESPACE_URL, uuid5
from zoneinfo import ZoneInfo

from .applications import seed_catalog
from . import application_forms
from . import campus, courses, equipment, finances, individual_plan, practice, retakes, teams
from .auth import hash_password, token
from .db import write
from .domain import timestamp
from .learning import seed_assessments
from .reservations import seed_reservations
from .curriculum import seed_curriculum
from .lending import seed_library
from .portfolio import seed_opportunities
from .services import notify

UNIVERSITY = "spbstu-demo"
SOURCE = "spbstu-demo-seed-v1"
ZONE = ZoneInfo("Europe/Moscow")
ROSTER = {
    "student-a": ("Мира Яроветова", "student", "demo-ai"),
    "student-b": ("Лев Светолесов", "student", "demo-energy"),
    "staff": ("Вера Луноречьева", "staff", None),
}


def seed_id(label):
    return uuid5(NAMESPACE_URL, f"dean-demo:{UNIVERSITY}:{label}").hex


def credentials_path(db_path):
    return db_path.with_name(db_path.name + ".accounts.json")


def validate_passwords(accounts):
    if (not isinstance(accounts, dict) or set(accounts) != set(ROSTER)
            or not all(isinstance(value, str) and 16 <= len(value) <= 128 for value in accounts.values())
            or len(set(accounts.values())) != len(ROSTER)):
        raise ValueError("Нужны три разных пароля по 16–128 символов для student-a, student-b и staff.")
    return accounts


def credentials(path):
    if path.is_file():
        return validate_passwords(json.loads(path.read_text(encoding="utf-8")))
    accounts = {login: token()[:20] for login in ROSTER}
    with NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent, delete=False) as file:
        staged = Path(file.name)
        json.dump(accounts, file, indent=2)
        file.write("\n")
    try:
        os.link(staged, path)
    finally:
        staged.unlink(missing_ok=True)
    return accounts


def at(day, hour, minute=0):
    return timestamp(datetime.combine(day, time(hour, minute), ZONE))


def seed_extensions(db, anchor=None, expansion_date=None):
    version = db.execute("PRAGMA user_version").fetchone()[0]
    if version >= 3:
        seed_catalog(db, UNIVERSITY)
    if version >= 4:
        seed_assessments(db, UNIVERSITY)
    if version >= 6:
        seed_reservations(db, UNIVERSITY)
    if version >= 7:
        seed_reservations(db, UNIVERSITY, activities=True)
        seed_library(db, UNIVERSITY)
        seed_opportunities(db, UNIVERSITY)
    if version >= 8:
        application_forms.seed(db, UNIVERSITY, version)
    if version >= 9:
        preparation = expansion_date or anchor or datetime.now(ZONE).date()
        db.execute("INSERT INTO demo_expansion_date VALUES (?,?) ON CONFLICT DO NOTHING",
                   (UNIVERSITY, preparation.isoformat()))
        preparation = date.fromisoformat(db.execute(
            "SELECT day FROM demo_expansion_date WHERE university_id=?", (UNIVERSITY,)).fetchone()[0])
        retakes.seed(db, UNIVERSITY, preparation)
    if version >= 7:
        seed_curriculum(db, UNIVERSITY)
    if version >= 9:
        individual_plan.seed(db, UNIVERSITY, preparation)
    if version >= 10:
        finances.seed(db, UNIVERSITY, preparation)
    if version >= 11:
        equipment.seed(db, UNIVERSITY)
        campus.seed(db, UNIVERSITY)
    if version >= 12:
        teams.seed(db, UNIVERSITY, preparation)
    if version >= 13:
        practice.seed(db, UNIVERSITY, preparation)
        courses.seed(db, UNIVERSITY, preparation)


def seed(db, path, anchor=None, passwords=None, *, expansion_date=None):
    with write(db):
        existing = db.execute("SELECT 1 FROM university WHERE id = ?", (UNIVERSITY,)).fetchone()
        if existing:
            seed_extensions(db, anchor, expansion_date)
            return
        accounts = credentials(credentials_path(path)) if passwords is None else validate_passwords(passwords)

        anchor = anchor or datetime.now(ZONE).date()
        previous_year = anchor.year - 1
        start = date(previous_year, 9, 1) if anchor.month < 2 else date(anchor.year, 2, 1)
        if anchor.month >= 8:
            start = date(anchor.year, 8, 1)
        end = max(anchor + timedelta(days=60), start + timedelta(days=120))
        semester = "Осенний" if anchor.month == 1 or anchor.month >= 8 else "Весенний"
        period = f"demo-{start.year}-{start.month:02}"
        now = timestamp()

        db.execute("INSERT INTO university VALUES (?, 'Учебный кампус', 'Europe/Moscow', 'demo_university')", (UNIVERSITY,))
        db.execute("INSERT INTO academic_period VALUES (?, ?, ?, ?, ?)",
                   (UNIVERSITY, period, f"{semester} семестр · {start.year}", start.isoformat(), end.isoformat()))
        db.execute("INSERT INTO academic_period VALUES (?, ?, ?, ?, ?)",
                   (UNIVERSITY, "demo-previous", "Предыдущий семестр",
                    (start - timedelta(days=150)).isoformat(), (start - timedelta(days=1)).isoformat()))
        db.executemany("INSERT INTO program VALUES (?, ?, ?, ?)", [
            (UNIVERSITY, "ai", "Институт компьютерных наук и кибербезопасности", "Прикладная информатика"),
            (UNIVERSITY, "energy", "Институт энергетики", "Электроэнергетика и электротехника"),
        ])
        db.executemany("INSERT INTO study_group VALUES (?, ?, ?, ?, ?)", [
            (UNIVERSITY, "demo-ai", "ДЕМО-ИКН-21", "ai", 2),
            (UNIVERSITY, "demo-energy", "ДЕМО-ИЭ-11", "energy", 1),
        ])
        for login, (name, role, group) in ROSTER.items():
            db.execute("""
                INSERT INTO account (id, username, password_hash, full_name, role, university_id, group_id, period_id)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (seed_id(login), login, hash_password(accounts[login]), name, role, UNIVERSITY,
                  group, period if group else None))
        db.execute("INSERT INTO staff_group VALUES (?, ?, 'demo-ai')", (UNIVERSITY, seed_id("staff")))
        db.executemany("INSERT INTO subject VALUES (?, ?, ?)", [
            (UNIVERSITY, "algorithms", "Алгоритмы и структуры данных"),
            (UNIVERSITY, "databases", "Базы данных"),
            (UNIVERSITY, "english", "Иностранный язык"),
            (UNIVERSITY, "physics", "Общая физика"),
            (UNIVERSITY, "circuits", "Основы электрических цепей"),
            (UNIVERSITY, "graphics", "Инженерная графика"),
        ])
        lessons = [
            ("a1", "demo-ai", "algorithms", 0, 10, "Семинар", "ДЕМО · А-214", "Ада Кремнева"),
            ("a2", "demo-ai", "databases", 0, 13, "Лекция", "ДЕМО · А-108", "Олег Полярисов"),
            ("a3", "demo-ai", "english", 1, 12, "Практика", "ДЕМО · Б-102", "Нина Ясноречевая"),
            ("a4", "demo-ai", "algorithms", 3, 10, "Лекция", "ДЕМО · А-214", "Ада Кремнева"),
            ("a5", "demo-ai", "databases", 7, 13, "Практика", "ДЕМО · А-108", "Олег Полярисов"),
            ("b1", "demo-energy", "physics", 0, 9, "Лекция", "ДЕМО · В-205", "Иван Грозосветов"),
            ("b2", "demo-energy", "circuits", 2, 11, "Лабораторная", "ДЕМО · В-310", "Лада Меднолучева"),
            ("b3", "demo-energy", "graphics", 4, 15, "Практика", "ДЕМО · В-225", "Тимур Клёновектор"),
        ]
        for ref, group, subject, offset, hour, kind, room, teacher in lessons:
            day = anchor + timedelta(days=offset)
            db.execute("""
                INSERT INTO schedule_event
                    (id, university_id, group_id, period_id, subject_id, starts_at, ends_at,
                     timezone, kind, room, teacher, source_id, source_event_id, origin)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'demo_university')
            """, (seed_id(ref), UNIVERSITY, group, period, subject, at(day, hour), at(day, hour + 1, 30),
                  ZONE.key, kind, room, teacher, SOURCE, ref))
        for student, subject, kind, score, outcome, exam, term in [
            ("student-a", "algorithms", "exam", 86, "passed", None, period),
            ("student-a", "databases", "exam", None, None, at(anchor + timedelta(days=4), 11), period),
            ("student-a", "english", "credit", None, "passed", None, period),
            ("student-a", "english", "credit", None, "passed", None, "demo-previous"),
            ("student-b", "physics", "exam", 0, "failed", None, period),
            ("student-b", "circuits", "exam", None, None, at(anchor + timedelta(days=6), 10), period),
            ("student-b", "graphics", "credit", None, None, None, period),
        ]:
            db.execute("INSERT INTO learning_result (id, university_id, student_id, period_id, subject_id, kind, score, outcome, exam_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                       (seed_id(f"{student}:{subject}:{term}"), UNIVERSITY, seed_id(student),
                        term, subject, kind, score, outcome, exam))
        db.executemany("INSERT INTO service (university_id, id, name, description, placeholder) VALUES (?, ?, ?, ?, ?)", [
            (UNIVERSITY, "consultation", "Консультация деканата",
             "Опиши учебный вопрос. Сотрудник оставит ответ в истории заявки.",
             "Например, хочу узнать, как перенести зачёт."),
            (UNIVERSITY, "data-question", "Уточнение учебных данных",
             "Сообщи о расхождении в учебных данных или расписании.",
             "Укажи дисциплину и данные, которые нужно проверить."),
        ])
        b_application = seed_id("b-application")
        b_student = seed_id("student-b")
        db.execute("INSERT INTO application (id, university_id, student_id, group_id, service_id, details, status, created_at, updated_at) VALUES (?, ?, ?, 'demo-energy', 'data-question', ?, 'submitted', ?, ?)",
                   (b_application, UNIVERSITY, b_student, "Уточнить дату экзамена по электрическим цепям.", now, now))
        db.execute("""
            INSERT INTO application_history (application_id, actor_id, status, note, created_at)
            VALUES (?, ?, 'submitted', '', ?)
        """, (b_application, b_student, now))
        notify(db, UNIVERSITY, b_student, "application", b_application, f"application:{b_application}:submitted",
               "Заявка отправлена", "Уточнение учебных данных", now)
        aid = seed_id("welcome")
        title = "Консультации по учебным вопросам"
        db.execute("INSERT INTO announcement VALUES (?, ?, 'demo-ai', ?, ?, ?, ?)",
                   (aid, UNIVERSITY, seed_id("staff"), title,
                    "Вопросы о зачётах и занятиях можно отправить через раздел «Заявки».", now))
        notify(db, UNIVERSITY, seed_id("student-a"), "announcement", aid, f"announcement:{aid}",
               title, "Объявление деканата", now)

        seed_extensions(db, anchor, expansion_date)
