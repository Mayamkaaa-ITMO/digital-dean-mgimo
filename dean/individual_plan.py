import json
from datetime import timedelta

from . import curriculum, services as svc
from .domain import DomainError, require, timestamp
from .fixtures import demo_id
from .schedule import local_midnight
from .workspace import scope

RULES = {"all": "Все дисциплины", "one": "Одна дисциплина", "credits": "Заданная сумма кредитов"}


def seed(db, university, anchor):
    groups = list(db.execute("""
        SELECT DISTINCT c.program_id,c.period_id,t.timezone FROM curriculum_item c
        JOIN university t ON t.id=c.university_id WHERE c.university_id=?
    """, (university,)))
    for group in groups:
        program, period, zone = group
        keys = (university, program, period)
        for rule, label, target, legacy in (
            ("all", "Обязательный блок", None, 0),
            ("one", "Факультатив", None, 1),
            ("credits", "Модуль практических навыков", 4, 0),
        ):
            bid = demo_id(university, "curriculum-block", f"{program}:{period}:{rule}")
            db.execute("INSERT INTO curriculum_block VALUES (?,?,?,?,?,?,?,?) ON CONFLICT DO NOTHING",
                       (bid, *keys, label, rule, target, legacy))
            if rule != "credits":
                kind = "required" if rule == "all" else "elective"
                db.execute("""
                    INSERT INTO curriculum_block_item
                    SELECT ?,c.id FROM curriculum_item c WHERE c.university_id=? AND c.program_id=? AND c.period_id=?
                    AND c.kind=? AND NOT EXISTS (SELECT 1 FROM curriculum_block_item b WHERE b.item_id=c.id)
                    ON CONFLICT DO NOTHING
                """, (bid, *keys, kind))
                continue
            first = local_midnight(anchor - timedelta(days=1), zone)
            last = local_midnight(anchor + timedelta(days=21), zone)
            for index, title, credits, prerequisite, topics in (
                (1, "Учебный проект · лаборатория", 4, "english" if program == "ai" else None,
                 "Постановка задачи, эксперимент, защита результатов"),
                (2, "Представление результатов", 2, None, "Структура доклада, презентация, обратная связь"),
                (3, "Командное проектирование", 2, None, "Декомпозиция, контроль версий, проверка результата"),
            ):
                iid = demo_id(university, "curriculum-course", f"{bid}:{index}")
                db.execute("""
                    INSERT INTO curriculum_item
                        (id,university_id,program_id,period_id,title,credits,kind,window_start,window_end,capacity)
                    VALUES (?,?,?,?,?,?,'elective',?,?,?) ON CONFLICT DO NOTHING
                """, (iid, *keys, title, credits, first, last, 1 if index == 1 else 8))
                db.execute("INSERT INTO curriculum_block_item VALUES (?,?) ON CONFLICT DO NOTHING", (bid, iid))
                db.execute("INSERT INTO curriculum_course VALUES (?,?,?,?,?,?) ON CONFLICT DO NOTHING",
                           (iid, topics, credits * 36, "Зачёт с демонстрацией работы", prerequisite, university))
    db.execute("""
        INSERT INTO curriculum_course
        SELECT c.id,'Основные понятия, практические работы и разбор примеров.',c.credits*36,
               'Контрольные работы и итоговая аттестация',NULL,c.university_id
        FROM curriculum_item c WHERE c.university_id=? ON CONFLICT DO NOTHING
    """, (university,))


def blocks(db, user, bid=None):
    university, _ = scope(user)
    query = """
        SELECT b.* FROM curriculum_block b JOIN study_group g ON g.university_id=b.university_id
        AND g.program_id=b.program_id AND g.id=? WHERE b.university_id=? AND b.period_id=?
    """
    args = (user["group_id"], university, user["period_id"])
    if bid:
        return require(db.execute(query + " AND b.id=?", (*args, bid)).fetchone())
    return list(db.execute(query + " ORDER BY b.rule", args))


def courses(db, user, block):
    university, actor = scope(user)
    return list(db.execute("""
        SELECT c.*,d.topics,d.hours,d.assessment,d.prerequisite_subject,s.name AS prerequisite,
           EXISTS(SELECT 1 FROM curriculum_selection x WHERE x.university_id=c.university_id
                  AND x.item_id=c.id AND x.student_id=?) AS selected,
           (SELECT count(*) FROM curriculum_selection x WHERE x.university_id=c.university_id AND x.item_id=c.id) AS enrolled
        FROM curriculum_block_item b JOIN curriculum_item c ON c.id=b.item_id
        JOIN curriculum_course d ON d.item_id=c.id
        LEFT JOIN subject s ON s.university_id=c.university_id AND s.id=d.prerequisite_subject
        WHERE b.block_id=? AND c.university_id=? ORDER BY c.title
    """, (actor, block["id"], university)))


def course(db, user, iid):
    university, _ = scope(user)
    return require(db.execute("""
        SELECT c.*,d.topics,d.hours,d.assessment,s.name AS prerequisite
        FROM curriculum_item c JOIN curriculum_course d ON d.item_id=c.id
        JOIN study_group g ON g.university_id=c.university_id AND g.program_id=c.program_id AND g.id=?
        LEFT JOIN subject s ON s.university_id=c.university_id AND s.id=d.prerequisite_subject
        WHERE c.university_id=? AND c.id=?
    """, (user["group_id"], university, iid)).fetchone())


def eligible(db, user, row):
    if not row["prerequisite_subject"]:
        return True
    return bool(db.execute("""
        SELECT 1 FROM learning_result r JOIN academic_period p ON p.university_id=r.university_id AND p.id=r.period_id
        JOIN academic_period current ON current.university_id=r.university_id AND current.id=?
        WHERE r.university_id=? AND r.student_id=? AND r.subject_id=? AND r.outcome='passed'
        AND p.starts_on<current.starts_on LIMIT 1
    """, (user["period_id"], *scope(user), row["prerequisite_subject"])).fetchone())


def check_rule(block, rows, selected):
    indexed = {row["id"]: row for row in rows}
    if not selected <= indexed.keys():
        raise DomainError(422, "Выбери дисциплины своего блока и программы.")
    rule = block["rule"]
    if rule == "all" and selected != indexed.keys():
        raise DomainError(422, "В обязательном блоке нужны все дисциплины.")
    if rule == "one" and len(selected) != 1:
        raise DomainError(422, "Выбери один факультатив.")
    if rule == "credits" and sum(indexed[i]["credits"] for i in selected) != block["credit_target"]:
        raise DomainError(422, f'Выбери дисциплины ровно на {block["credit_target"]} кредита.')


def select(db, user, bid, selected, key):
    selected = set(selected)

    def apply():
        block = blocks(db, user, bid)
        if block["rule"] != "credits":
            raise DomainError(422, "Этот блок изменяется через свой раздел учебного плана.")
        rows = courses(db, user, block)
        check_rule(block, rows, selected)
        current = {row["id"] for row in rows if row["selected"]}
        if not all(eligible(db, user, row) for row in rows if row["id"] in selected):
            raise DomainError(422, "Для выбранной дисциплины не выполнено входное требование.")
        if selected == current:
            return bid
        now = timestamp()
        for row in rows:
            if row["id"] not in current | selected:
                continue
            if not row["window_start"] <= now < row["window_end"]:
                raise DomainError(409, "Окно выбора сейчас закрыто.")
            if row["id"] in selected - current and row["enrolled"] >= row["capacity"]:
                raise DomainError(409, "На выбранном потоке больше нет мест.")
        for iid in current - selected:
            db.execute("DELETE FROM curriculum_selection WHERE university_id=? AND student_id=? AND item_id=?",
                       (*scope(user), iid))
        db.executemany("INSERT INTO curriculum_selection VALUES (?,?,?,?)",
                       [(*scope(user), iid, now) for iid in sorted(selected - current)])
        return bid

    return svc.command(db, user, "curriculum.block.select", key, [bid, sorted(selected)], apply)


def snapshot(db, user, *, required):
    result = []
    for block in blocks(db, user):
        rows = courses(db, user, block)
        if not rows:
            continue
        if block["rule"] == "all":
            selected = {r["id"] for r in rows}
        elif block["uses_existing_elective"]:
            chosen = db.execute("SELECT item_id FROM curriculum_choice WHERE university_id=? AND student_id=? AND period_id=?",
                                (*scope(user), user["period_id"])).fetchone()
            selected = {chosen[0]} if chosen else set()
        else:
            selected = {r["id"] for r in rows if r["selected"]}
        if required:
            check_rule(block, rows, selected)
            for row in rows:
                if row["id"] in selected and not eligible(db, user, row):
                    raise DomainError(422, "Одно из входных требований плана больше не выполнено.")
        result.append({"block": block["title"], "rule": block["rule"], "items": [
            {"id": r["id"], "title": r["title"], "credits": r["credits"]} for r in rows if r["id"] in selected
        ]})
    if required and not result:
        raise DomainError(422, "Сначала выбери дисциплины в учебном плане.")
    return result


def approve(db, user, row, now):
    if row["assignee_id"] != user["id"]:
        raise DomainError(403, "Решение принимает назначенный рецензент.")
    from .applications import answers
    snapshot_data = answers(row).get("_plan")
    if not snapshot_data:
        raise DomainError(409, "Отправленная версия учебного плана отсутствует.")
    db.execute("INSERT INTO individual_plan_approval VALUES (?,?,?,?)",
               (row["id"], user["id"], json.dumps(snapshot_data, ensure_ascii=False), now))


def approved(db, user):
    return list(db.execute("""
        SELECT p.*,a.id FROM individual_plan_approval p JOIN application a ON a.id=p.application_id
        WHERE a.university_id=? AND a.student_id=? ORDER BY p.approved_at DESC,p.rowid DESC
    """, scope(user)))
