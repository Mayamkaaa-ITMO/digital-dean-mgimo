from datetime import timedelta

from . import services as svc
from .domain import DomainError, field, identifier, require, role, timestamp
from .fixtures import demo_id, demo_time

RESULTS = {"passed": "Пересдача сдана", "failed": "Пересдача не сдана", "absent": "Неявка"}
DEBT = """
    SELECT d.*,r.subject_id,r.period_id,r.student_id,r.university_id,s.name AS subject,
           a.full_name,a.group_id
    FROM retake_debt d JOIN learning_result r ON r.id=d.id
    JOIN subject s ON s.id=r.subject_id AND s.university_id=r.university_id
    JOIN account a ON a.id=r.student_id AND a.university_id=r.university_id
    WHERE r.university_id=?
"""


def seed(db, university, anchor):
    student = db.execute("""
        SELECT a.id,a.group_id,p.id AS previous FROM account a
        JOIN academic_period p ON p.university_id=a.university_id AND p.id='demo-previous'
        WHERE a.university_id=? AND a.username='student-a'
    """, (university,)).fetchone()
    staff = db.execute("""
        SELECT a.id FROM account a JOIN staff_group g ON g.staff_id=a.id AND g.university_id=a.university_id
        WHERE a.university_id=? AND a.username='staff' AND g.group_id=?
    """, (university, student["group_id"] if student else "")).fetchone()
    if not student or not staff:
        return
    subject_id = "demo-practicum"
    title = "Учебный практикум (демо)"
    result = demo_id(university, "retake", "previous-practicum")
    db.execute("INSERT INTO subject VALUES (?,?,?) ON CONFLICT DO NOTHING", (university, subject_id, title))
    db.execute("""
        INSERT INTO learning_result (id,university_id,student_id,period_id,subject_id,kind,outcome)
        VALUES (?,?,?,?,?,'credit','failed') ON CONFLICT DO NOTHING
    """, (result, university, student["id"], student["previous"], subject_id))
    db.execute("""
        INSERT INTO retake_debt (id,reason,conditions)
        SELECT id,'Не сдан итог демонстрационного практикума','Проверка отчёта и допуск учебной части.'
        FROM learning_result WHERE id=? AND outcome='failed' ON CONFLICT DO NOTHING
    """, (result,))
    bid = demo_id(university, "retake", "previous-practicum-offer")
    db.execute("""
        INSERT INTO bookable VALUES (?,?,?,'consultation',?,'Пересдачи','ДЕМО · А-104','','После подтверждения допуска.','')
        ON CONFLICT DO NOTHING
    """, (bid, university, staff["id"], "Пересдача · " + title))
    db.execute("INSERT INTO retake_offer VALUES (?,?,?,?) ON CONFLICT DO NOTHING",
               (bid, university, subject_id, student["group_id"]))
    for offset in (2, 5):
        day = anchor + timedelta(days=offset)
        db.execute("""
            INSERT INTO reservation_slot (id,university_id,bookable_id,starts_at,ends_at,capacity)
            VALUES (?,?,?,?,?,1) ON CONFLICT DO NOTHING
        """, (demo_id(university, "retake", f"slot-{offset}"), university, bid,
              demo_time(day, 16), demo_time(day, 17)))
    db.execute("""
        INSERT INTO retake_debt (id,reason,conditions)
        SELECT r.id,'Итоговый экзамен не сдан','Допуск подтверждает сотрудник твоей группы.'
        FROM learning_result r JOIN account a ON a.id=r.student_id AND a.university_id=r.university_id
        WHERE r.university_id=? AND a.username='student-b' AND r.subject_id='physics' AND r.outcome='failed'
        ON CONFLICT DO NOTHING
    """, (university,))


def debts(db, user, did=None, *, manage=False):
    query = DEBT
    params = (user["university_id"], user["id"])
    if manage:
        role(user, "staff")
        query += """ AND EXISTS (SELECT 1 FROM staff_group g WHERE g.university_id=r.university_id
            AND g.group_id=a.group_id AND g.staff_id=?)"""
    else:
        role(user, "student")
        query += " AND r.student_id=?"
    if did:
        return require(db.execute(query + " AND d.id=?", (*params, did)).fetchone())
    return list(db.execute(query + " ORDER BY d.closed_at IS NOT NULL,s.name", params))


def open_debt(db, user, rid, inputs, key):
    from .learning import staff_results

    reason = field(inputs.get("reason", ""), "Причина", 300)
    conditions = field(inputs.get("conditions", ""), "Условия допуска", 500)
    component = inputs.get("assessment", "")

    def apply():
        row = staff_results(db, user, rid)
        missed = component and db.execute("""
            SELECT 1 FROM assessment WHERE id=? AND result_id=? AND kind='coursework'
            AND due_at<? AND state IN ('ungraded','failed')
        """, (component, rid, timestamp())).fetchone()
        if row["outcome"] != "failed" and not missed:
            raise DomainError(422, "Нужен подтверждённый несданный итог или просроченная несданная работа.")
        changed = db.execute("""
            INSERT INTO retake_debt (id,reason,conditions) VALUES (?,?,?) ON CONFLICT DO NOTHING
        """, (rid, reason, conditions)).rowcount
        if changed:
            svc.notify(db, row["university_id"], row["student_id"], "retake", rid,
                       f"retake:{rid}:opened", "Открыта задолженность", reason, timestamp())
        return rid

    return svc.command(db, user, "retake.open", key, [rid, component, reason, conditions], apply)


def offers(db, row):
    return list(db.execute("""
        SELECT b.id,b.title,b.manager_id FROM retake_offer t
        JOIN bookable b ON b.id=t.id AND b.university_id=t.university_id
        WHERE t.university_id=? AND t.subject_id=? AND t.group_id=? ORDER BY b.title
    """, (row["university_id"], row["subject_id"], row["group_id"])))


def create_offer(db, user, did, place, key):
    place = field(place, "Место пересдачи", 120)

    def apply():
        row = debts(db, user, did, manage=True)
        existing = next((offer for offer in offers(db, row) if offer["manager_id"] == user["id"]), None)
        if existing:
            return existing["id"]
        bid = identifier()
        title = "Пересдача · " + row["subject"]
        db.execute("INSERT INTO bookable VALUES (?,?,?,'consultation',?,'Пересдачи',?,'',?,'')",
                   (bid, row["university_id"], user["id"], title, place, row["conditions"]))
        db.execute("INSERT INTO retake_offer VALUES (?,?,?,?)",
                   (bid, row["university_id"], row["subject_id"], row["group_id"]))
        return bid

    return svc.command(db, user, "retake.offer", key, [did, place], apply)


def matching_debt(db, user, bid, *, require_admission=False):
    role(user, "student")
    row = require(db.execute(DEBT + """
        AND r.student_id=? AND EXISTS (
            SELECT 1 FROM retake_offer o WHERE o.id=? AND o.university_id=r.university_id
            AND o.subject_id=r.subject_id AND o.group_id=a.group_id
        ) ORDER BY d.closed_at IS NOT NULL,d.admitted DESC,r.period_id DESC LIMIT 1
    """, (user["university_id"], user["id"], bid)).fetchone())
    if require_admission and (not row["admitted"] or row["closed_at"]):
        raise DomainError(409, "Сначала нужен действующий допуск к пересдаче.")
    return row


def attempts(db, row):
    return list(db.execute("""
        SELECT a.*,r.starts_at,r.ends_at,r.status,b.place FROM retake_booking a
        JOIN reservation r ON r.id=a.reservation_id
        JOIN reservation_slot s ON s.id=r.slot_id AND s.university_id=r.university_id
        JOIN bookable b ON b.id=s.bookable_id AND b.university_id=r.university_id
        WHERE a.debt_id=? ORDER BY r.starts_at DESC
    """, (row["id"],)))


def check_new_attempt(db, user, bid, starts, ends):
    from .reservations import has_conflict
    debt = matching_debt(db, user, bid, require_admission=True)
    if any(row["status"] == "reserved" and not row["result"] for row in attempts(db, debt)):
        raise DomainError(409, "У тебя уже есть запись на пересдачу этой задолженности.")
    if has_conflict(db, user, starts, ends):
        raise DomainError(409, "Время пересекается с твоим расписанием. Выбери другое окно.")
    return debt["id"]


def admit(db, user, did, allowed, reason, key):
    reason = field(reason, "Комментарий к допуску", 300)

    def apply():
        row = debts(db, user, did, manage=True)
        if row["closed_at"]:
            raise DomainError(409, "Задолженность уже закрыта.")
        if row["admitted"] == allowed:
            return did
        if not allowed and any(r["status"] == "reserved" and not r["result"] for r in attempts(db, row)):
            raise DomainError(409, "Сначала нужно отменить действующую запись на пересдачу.")
        hid, now = identifier(), timestamp()
        db.execute("UPDATE retake_debt SET admitted=? WHERE id=?", (allowed, did))
        db.execute("INSERT INTO retake_admission_history VALUES (?,?,?,?,?,?)",
                   (hid, did, user["id"], allowed, reason, now))
        svc.notify(db, row["university_id"], row["student_id"], "retake", did, f"retake:{hid}",
                   "Допуск к пересдаче подтверждён" if allowed else "Допуск отозван", reason, now)
        return did

    return svc.command(db, user, "retake.admit", key, [did, allowed, reason], apply)


def record_result(db, user, did, rid, result, reason, key):
    from .reservations import slot
    role(user, "staff")
    if result not in RESULTS:
        raise DomainError(422, "Выбери результат пересдачи.")
    reason = field(reason, "Комментарий к результату", 500)

    def apply():
        debt = debts(db, user, did, manage=True)
        booking = require(db.execute("""
            SELECT a.*,r.slot_id,r.status,r.ends_at FROM retake_booking a JOIN reservation r ON r.id=a.reservation_id
            WHERE a.reservation_id=? AND a.debt_id=? AND r.university_id=?
        """, (rid, did, user["university_id"])).fetchone())
        slot(db, user, booking["slot_id"], manage=True)
        if booking["result"] == result and booking["reason"] == reason:
            return did
        if debt["closed_at"] or booking["result"] or booking["status"] != "reserved" or booking["ends_at"] > timestamp():
            raise DomainError(409, "Результат можно внести один раз после окончания подтверждённой пересдачи.")
        now = timestamp()
        db.execute("""
            UPDATE retake_booking SET result=?,reason=?,decided_at=?,decided_by=? WHERE reservation_id=?
        """, (result, reason, now, user["id"], rid))
        if result == "passed":
            db.execute("UPDATE retake_debt SET closed_at=? WHERE id=?", (now, did))
        svc.notify(db, debt["university_id"], debt["student_id"], "retake", did, f"retake:{rid}:result",
                   RESULTS[result], reason, now)
        return did

    return svc.command(db, user, "retake.result", key, [did, rid, result, reason], apply)
