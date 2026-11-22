from datetime import timedelta

from . import services as svc
from .domain import DomainError, require, timestamp
from .fixtures import demo_id, demo_time
from .workspace import scope

COURSES = (
    ("short-study", "Проверяем учебный эксперимент", "Учёба", "Лаборатория демо",
     "Два коротких урока о проверке гипотезы.",
     (("Сформулируй вопрос", "Запиши одно сравнение, которое хочешь проверить. Укажи, что меняется и что измеряешь."),
      ("Честно сравни результаты", "Проведи оба опыта в одинаковых условиях. Отдельно запиши наблюдение и объяснение."))),
    ("project-notes", "Журнал учебного проекта", "Проекты", "Команда демо",
     "Небольшой курс о ведении проектных записей.",
     (("Веди журнал шагов", "Укажи дату, проверенное действие и результат. Запиши, что осталось узнать."),
      ("Подготовь отчёт", "Сопоставь результат с целью. Выбери два проверяемых примера и перечисли ограничения."))),
)


def seed(db, university, anchor):
    until = demo_time(anchor + timedelta(days=28), 18)
    for key, title, category, author, description, lessons in COURSES:
        cid = demo_id(university, "course", key)
        db.execute("INSERT INTO demo_course VALUES (?,?,?,?,?,?,?) ON CONFLICT DO NOTHING",
                   (cid, university, title, category, author, description, until))
        for index, (lesson, content) in enumerate(lessons, 1):
            db.execute("INSERT INTO demo_course_lesson VALUES (?,?,?,?,?) ON CONFLICT DO NOTHING",
                       (demo_id(university, "lesson", f"{cid}:{index}"), cid, index, lesson, content))


def listing(db, user, cid=None, *, q="", category="", favorites=False):
    university, student = scope(user)
    query = """
        SELECT c.*,s.favorite,s.enrolled_at,
            (SELECT count(*) FROM demo_course_lesson l WHERE l.course_id=c.id) AS lessons,
            (SELECT count(*) FROM demo_course_lesson l JOIN demo_course_progress p ON p.lesson_id=l.id
             WHERE l.course_id=c.id AND p.student_id=? AND p.university_id=c.university_id) AS finished
        FROM demo_course c LEFT JOIN demo_course_student s ON s.course_id=c.id AND s.university_id=c.university_id
        AND s.student_id=? WHERE c.university_id=?
    """
    args = (student, student, university)
    if cid:
        return require(db.execute(query + " AND c.id=?", (*args, cid)).fetchone())
    term = q[:100].strip().casefold()
    return [row for row in db.execute(query + " ORDER BY c.category,c.title", args)
            if (not category or category == row["category"]) and (not favorites or row["favorite"])
            and term in (row["title"] + " " + row["description"]).casefold()]


def course_action(db, user, cid, action, key):
    def apply():
        course = listing(db, user, cid)
        university, student = scope(user)
        now = timestamp()
        if action == "enroll":
            if course["enrolled_at"]:
                return cid
            if course["closes_at"] <= now:
                raise DomainError(409, "Запись на курс завершилась.")
            db.execute("""
                INSERT INTO demo_course_student (university_id,student_id,course_id,enrolled_at) VALUES (?,?,?,?)
                ON CONFLICT(university_id,student_id,course_id) DO UPDATE SET enrolled_at=excluded.enrolled_at
            """, (university, student, cid, now))
            svc.notify(db, university, student, "course", cid, f"course:{cid}:enrolled", "Запись на курс подтверждена",
                       course["title"], now)
        else:
            db.execute("""
                INSERT INTO demo_course_student (university_id,student_id,course_id,favorite) VALUES (?,?,?,?)
                ON CONFLICT(university_id,student_id,course_id) DO UPDATE SET favorite=excluded.favorite
            """, (university, student, cid, action == "favorite"))
        return cid
    return svc.command(db, user, "course.action", key, [cid, action], apply)


def lessons(db, user, cid):
    course = listing(db, user, cid)
    if not course["enrolled_at"]:
        return []
    return list(db.execute("""
        SELECT l.*,p.finished_at FROM demo_course_lesson l LEFT JOIN demo_course_progress p ON p.lesson_id=l.id
        AND p.university_id=? AND p.student_id=? WHERE l.course_id=? ORDER BY l.position
    """, (*scope(user), cid)))


def lesson(db, user, cid, lid):
    return require(next((item for item in lessons(db, user, cid) if item["id"] == lid), None))


def finish(db, user, cid, lid, key):
    def apply():
        record = lesson(db, user, cid, lid)
        if record["finished_at"]:
            return lid
        now = timestamp()
        db.execute("INSERT INTO demo_course_progress VALUES (?,?,?,?)", (*scope(user), lid, now))
        course = listing(db, user, cid)
        if course["finished"] == course["lessons"]:
            svc.notify(db, *scope(user), "course", cid, f"course:{cid}:finished", "Курс завершён", course["title"], now)
        return lid
    return svc.command(db, user, "course.lesson.finish", key, [cid, lid], apply)
