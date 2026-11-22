from datetime import datetime, timedelta

from . import services as svc
from .domain import DomainError, field, identifier, require, role, timestamp
from .fixtures import demo_id
from .schedule import edit_time
from .workspace import scope

STATES = {"reserved": "Зарезервировано", "issued": "Выдано", "returned": "Возвращено", "cancelled": "Отменено"}


def seed(db, university):
    manager = db.execute("SELECT id FROM account WHERE university_id=? AND username='staff'", (university,)).fetchone()
    if not manager:
        return
    for label, title, pickup, return_place in (
        ("Н-01", "Ноутбук «Лаборатория»", "ДЕМО · стойка А-104", "ДЕМО · стойка А-104"),
        ("П-01", "Проектор «Семинар»", "ДЕМО · сервисная Б-105", "ДЕМО · сервисная Б-105"),
    ):
        db.execute("""
            INSERT INTO equipment_unit (id,university_id,manager_id,label,title,pickup,return_place)
            VALUES (?,?,?,?,?,?,?) ON CONFLICT DO NOTHING
        """, (demo_id(university, "equipment", label), university, manager[0], label, title, pickup, return_place))


def units(db, user, uid=None, *, manage=False):
    role(user, "staff" if manage else "student")
    query = """
        SELECT u.*,(SELECT count(*) FROM equipment_loan r WHERE r.unit_id=u.id
            AND (r.state='issued' OR (r.state='reserved' AND r.ends_at>?))) AS active
        FROM equipment_unit u WHERE u.university_id=?
    """
    args = (timestamp(), user["university_id"])
    if manage:
        query += " AND u.manager_id=?"
        args += (user["id"],)
    else:
        query += """ AND NOT EXISTS (
            SELECT 1 FROM managed_equipment m WHERE m.unit_id=u.id AND m.group_id<>?)"""
        args += (user["group_id"],)
    if uid:
        return require(db.execute(query + " AND u.id=?", (*args, uid)).fetchone())
    return list(db.execute(query + " ORDER BY u.title,u.label", args))


def loans(db, user, lid=None, *, manage=False):
    role(user, "staff" if manage else "student")
    query = """
        SELECT r.*,u.title,u.label,a.full_name FROM equipment_loan r
        JOIN equipment_unit u ON u.id=r.unit_id AND u.university_id=r.university_id
        JOIN account a ON a.id=r.student_id AND a.university_id=r.university_id
        WHERE r.university_id=?
    """
    query += " AND u.manager_id=?" if manage else " AND r.student_id=?"
    args = (user["university_id"], user["id"])
    if lid:
        return require(db.execute(query + " AND r.id=?", (*args, lid)).fetchone())
    return list(db.execute(query + " ORDER BY r.starts_at DESC,r.rowid DESC", args))


def history(db, row):
    return list(db.execute("""
        SELECT h.*,a.full_name FROM equipment_history h JOIN account a ON a.id=h.actor_id
        WHERE h.loan_id=? ORDER BY h.id
    """, (row["id"],)))


def occupied(db, unit, start, end):
    return db.execute("""
        SELECT 1 FROM equipment_loan WHERE unit_id=? AND university_id=? AND
        ((state='reserved' AND starts_at<? AND ends_at>?) OR
         (state='issued' AND (ends_at>? OR ends_at<=?))) LIMIT 1
    """, (unit["id"], unit["university_id"], end, start, start, timestamp())).fetchone()


def record(db, user, uid, lid, action, note, now):
    db.execute("""
        INSERT INTO equipment_history (unit_id,loan_id,actor_id,action,note,occurred_at) VALUES (?,?,?,?,?,?)
    """, (uid, lid, user["id"], action, note, now))


def reserve(db, user, uid, values, key):
    role(user, "student")
    start = edit_time(values.get("starts", ""), user["timezone"])
    end = edit_time(values.get("ends", ""), user["timezone"])
    if not timedelta(minutes=30) <= end - start <= timedelta(days=7):
        raise DomainError(422, "Выбери срок от 30 минут до семи дней.")
    start_at, end_at = timestamp(start), timestamp(end)
    purpose = field(values.get("purpose", ""), "Цель", 500, 5)
    software = field(values.get("software", ""), "Нужное ПО", 250, 0)

    def apply():
        unit = units(db, user, uid)
        now = timestamp()
        if start_at <= now or start > datetime.fromisoformat(now) + timedelta(days=30):
            raise DomainError(422, "Выбери время в ближайшие 30 дней.")
        if unit["maintenance"] or occupied(db, unit, start_at, end_at):
            raise DomainError(409, "Эта единица техники недоступна на выбранный срок.")
        lid = identifier()
        db.execute("""
            INSERT INTO equipment_loan VALUES (?,?,?,?,?,?,?,?,?,?,'reserved',?)
        """, (lid, *scope(user), uid, start_at, end_at, purpose, software, unit["pickup"], unit["return_place"], now))
        record(db, user, uid, lid, "reserved", "", now)
        svc.notify(db, *scope(user), "equipment", lid, f"equipment:{lid}:reserved",
                   "Техника зарезервирована", unit["title"], now)
        return lid

    return svc.command(db, user, "equipment.reserve", key, [uid, start_at, end_at, purpose, software], apply)


def change(db, user, lid, action, note, key):
    managing = action in {"issue", "return"}
    note = field(note, "Комментарий", 300, 0)
    transitions = {"issue": ("reserved", "issued"), "return": ("issued", "returned"),
                   "cancel": ("reserved", "cancelled")}

    def apply():
        row = loans(db, user, lid, manage=managing)
        old, new = transitions[action]
        if row["state"] == new:
            return lid
        if row["state"] != old:
            raise DomainError(409, "Переход недоступен для текущего состояния выдачи.")
        now = timestamp()
        if action == "issue":
            unit = units(db, user, row["unit_id"], manage=True)
            if (unit["maintenance"] or not row["starts_at"] <= now < row["ends_at"]
                    or db.execute("SELECT 1 FROM equipment_loan WHERE unit_id=? AND state='issued'",
                                  (row["unit_id"],)).fetchone()):
                raise DomainError(409, "Выдача доступна в срок резерва после возврата предыдущей единицы.")
        db.execute("UPDATE equipment_loan SET state=? WHERE id=?", (new, lid))
        record(db, user, row["unit_id"], lid, new, note, now)
        svc.notify(db, row["university_id"], row["student_id"], "equipment", lid, f"equipment:{lid}:{new}",
                   "Техника · " + STATES[new].lower(), row["title"], now)
        return lid

    return svc.command(db, user, "equipment.state", key, [lid, action, note], apply)


def maintenance(db, user, uid, enabled, reason, key):
    note = field(reason, "Причина", 300)

    def apply():
        row = units(db, user, uid, manage=True)
        if row["maintenance"] == enabled:
            return uid
        if enabled and row["active"]:
            raise DomainError(409, "Сначала заверши действующие резервы и выдачу.")
        db.execute("UPDATE equipment_unit SET maintenance=? WHERE id=?", (enabled, uid))
        record(db, user, uid, None, "maintenance" if enabled else "available", note, timestamp())
        return uid

    return svc.command(db, user, "equipment.maintenance", key, [uid, enabled, note], apply)
