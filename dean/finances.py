import re
from datetime import date, timedelta

from . import services as svc
from .domain import DomainError, field, identifier, require, role, timestamp
from .fixtures import demo_id, demo_time
from .schedule import edit_time, local_value
from .workspace import scope

AWARD_KINDS = {"stipend": "Стипендии", "work": "Выплаты за работу", "support": "Поддержка"}
CATEGORIES = {
    "income": {"work": "Подработка", "stipend": "Стипендия", "other": "Другой доход"},
    "expense": {"food": "Питание", "housing": "Жильё", "study": "Учёба", "other": "Другой расход"},
}
SUPPORT = {"material-aid": ("support", "Материальная помощь (демо)", 250_000),
           "enhanced-stipend": ("stipend", "Повышенная стипендия (демо)", 480_000)}


def rubles(amount):
    return f"{amount // 100:,}".replace(",", "\u00a0") + f",{amount % 100:02d} ₽"


def amount_minor(raw):
    if not re.fullmatch(r"[0-9]{1,7}(?:[,.][0-9]{1,2})?", raw):
        raise DomainError(422, "Укажи сумму в рублях с точностью до двух знаков.")
    main, _, decimal = raw.replace(",", ".").partition(".")
    amount = int(main) * 100 + int(decimal.ljust(2, "0"))
    if amount <= 0:
        raise DomainError(422, "Сумма должна быть больше нуля.")
    return amount


def seed(db, university, anchor):
    staff = db.execute("SELECT id FROM account WHERE university_id=? AND username='staff'", (university,)).fetchone()
    if not staff:
        return
    hall_id = demo_id(university, "finance", "residence-hall")
    db.execute("""
        INSERT INTO residence_hall VALUES (?,?,?,?,?,?) ON CONFLICT DO NOTHING
    """, (hall_id, university, "Общежитие «Спектр»", "Вымышленный кампус · корпус Д",
          "Демо-правила: часы тишины 23:00–07:00. Вопросы и ремонт через заявку.",
          "ДЕМО · стойка Д-01, по будням 10:00–16:00"))
    period = anchor.strftime("%Y-%m")
    stamp = demo_time(anchor, 9)
    for login, room, stipend, work, tuition, housing in (
        ("student-a", "Д-204", 315_050, 150_000, 1_230_045, 80_025),
        ("student-b", "Д-317", 270_075, 105_550, 980_010, 71_050),
    ):
        student = db.execute("""
            SELECT id,group_id FROM account WHERE university_id=? AND username=? AND role='student'
        """, (university, login)).fetchone()
        if not student:
            continue
        owner, group = student
        db.execute("INSERT INTO residence_stay VALUES (?,?,?,?,?,?,?) ON CONFLICT DO NOTHING",
                   (demo_id(university, "finance", login + ":stay"), university, owner, hall_id, room,
                    (anchor - timedelta(days=30)).isoformat(), (anchor + timedelta(days=90)).isoformat()))
        for category, title, amount in (
            ("stipend", "Академическая стипендия", stipend), ("work", "Работа в демо-лаборатории", work),
        ):
            aid = demo_id(university, "finance", f"{login}:{category}")
            db.execute("""
                INSERT INTO finance_award VALUES (?,?,?,?,?,?,?,?,?,NULL) ON CONFLICT DO NOTHING
            """, (aid, university, owner, group, category, title, period, amount, stamp))
            if category == "work":
                db.execute("""
                    INSERT INTO finance_disbursement VALUES (?,?,?,?) ON CONFLICT DO NOTHING
                """, (aid, amount, stamp, staff["id"]))
        for category, title, amount, delay in (
            ("tuition", "Начисление за обучение", tuition, 12),
            ("residence", "Начисление за проживание", housing, 6),
        ):
            cid = demo_id(university, "finance", f"{login}:charge:{category}")
            db.execute("INSERT INTO tuition_charge VALUES (?,?,?,?,?,?,?,?) ON CONFLICT DO NOTHING",
                       (cid, university, owner, category, title, period, amount, demo_time(anchor + timedelta(days=delay), 18)))


def awards(db, user, aid=None, *, manage=False):
    query = """
        SELECT a.*,p.paid_at,u.full_name FROM finance_award a
        LEFT JOIN finance_disbursement p ON p.award_id=a.id
        JOIN account u ON u.id=a.student_id AND u.university_id=a.university_id
        WHERE a.university_id=?
    """
    if manage:
        role(user, "staff")
        args = (user["university_id"], user["id"])
        query += """ AND EXISTS (SELECT 1 FROM staff_group g WHERE g.university_id=a.university_id
            AND g.group_id=a.group_id AND g.staff_id=?)"""
    else:
        args = scope(user)
        query += " AND a.student_id=?"
    if aid:
        return require(db.execute(query + " AND a.id=?", (*args, aid)).fetchone())
    return list(db.execute(query + " ORDER BY a.assigned_at DESC,a.rowid DESC", args))


def grant_support(db, user, row, now):
    category, title, amount = SUPPORT[row["service_id"]]
    aid = identifier()
    period = local_value(now, user["timezone"]).strftime("%Y-%m")
    changed = db.execute("""
        INSERT INTO finance_award VALUES (?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(source_app) DO NOTHING
    """, (aid, row["university_id"], row["student_id"], row["group_id"],
          category, title, period, amount, now, row["id"])).rowcount
    if changed:
        svc.notify(db, row["university_id"], row["student_id"], "award", aid, f"award:{aid}:assigned",
                   "Создано демо-назначение", title, now)


def disburse(db, user, aid, key):
    def apply():
        row = awards(db, user, aid, manage=True)
        if row["paid_at"]:
            return aid
        now = timestamp()
        db.execute("INSERT INTO finance_disbursement VALUES (?,?,?,?)",
                   (aid, row["amount"], now, user["id"]))
        svc.notify(db, row["university_id"], row["student_id"], "award", aid, f"award:{aid}:paid",
                   "Демо-выплата отмечена", row["title"], now)
        return aid

    return svc.command(db, user, "finance.disburse", key, [aid], apply)


def charges(db, user, cid=None):
    query = """
        SELECT c.*,p.id AS receipt_id,p.created_at AS paid_at FROM tuition_charge c
        LEFT JOIN demo_payment p ON p.charge_id=c.id AND p.state='success'
        WHERE c.university_id=? AND c.student_id=?
    """
    args = scope(user)
    if cid:
        return require(db.execute(query + " AND c.id=?", (*args, cid)).fetchone())
    return list(db.execute(query + " ORDER BY c.due_at,c.id", args))


def attempts(db, user, cid):
    charges(db, user, cid)
    return list(db.execute("SELECT * FROM demo_payment WHERE charge_id=? ORDER BY rowid DESC", (cid,)))


def pay(db, user, cid, result, key):
    if result not in {"success", "failed"}:
        raise DomainError(422, "Выбери исход симуляции.")

    def apply():
        row = charges(db, user, cid)
        if row["receipt_id"]:
            if result == "success":
                return row["receipt_id"]
            raise DomainError(409, "Начисление уже отмечено оплаченным.")
        pid, now = identifier(), timestamp()
        db.execute("INSERT INTO demo_payment VALUES (?,?,?,?,?)", (pid, cid, result, row["amount"], now))
        label = "Тестовая оплата учтена" if result == "success" else "Тестовая оплата не прошла"
        svc.notify(db, *scope(user), "charge", cid, f"payment:{pid}", label, row["title"], now)
        return pid

    return svc.command(db, user, "finance.pay", key, [cid, result], apply)


def receipt(db, user, pid):
    from .pdf import render_document

    row = require(db.execute("""
        SELECT p.*,c.title,c.period FROM demo_payment p JOIN tuition_charge c ON c.id=p.charge_id
        WHERE c.university_id=? AND c.student_id=? AND p.id=? AND p.state='success'
    """, (*scope(user), pid)).fetchone())
    lines = ["Симуляция оплаты. Реального списания не было.", f"Операция: {pid[:12]}",
             f"Начисление: {row['title']} · период {row['period']}", f"Сумма: {rubles(row['amount'])}",
             f"Дата: {local_value(row['created_at'], user['timezone']):%d.%m.%Y %H:%M}"]
    return render_document("Квитанция тестовой оплаты", lines)


def amounts_hidden(db, user):
    saved = db.execute("SELECT private FROM finance_visibility WHERE university_id=? AND student_id=?", scope(user)).fetchone()
    return not saved or bool(saved[0])


def set_visibility(db, user, private):
    db.execute("""
        INSERT INTO finance_visibility VALUES (?,?,?) ON CONFLICT(university_id,student_id)
        DO UPDATE SET private=excluded.private
    """, (*scope(user), private))


def preview(db, user):
    if amounts_hidden(db, user):
        return None
    return sum(row["amount"] for row in charges(db, user) if not row["paid_at"])


def residence(db, user):
    return list(db.execute("""
        SELECT r.*,h.title,h.location,h.rules,h.contact FROM residence_stay r
        JOIN residence_hall h ON h.id=r.hall_id AND h.university_id=r.university_id
        WHERE r.university_id=? AND r.student_id=? ORDER BY r.starts_on DESC
    """, scope(user)))


def budget(db, user):
    return list(db.execute("""
        SELECT * FROM budget_entry WHERE university_id=? AND student_id=? ORDER BY entry_date DESC,rowid DESC
    """, scope(user)))


def add_budget(db, user, values, key):
    direction, category = values.get("direction", ""), values.get("category", "")
    if category not in CATEGORIES.get(direction, {}):
        raise DomainError(422, "Выбери категорию для дохода или расхода.")
    title = field(values.get("title", ""), "Название записи", 120)
    amount = amount_minor(values.get("amount", ""))
    try:
        day = date.fromisoformat(values.get("day", "")).isoformat()
    except ValueError:
        raise DomainError(422, "Укажи дату записи.") from None
    raw = values.get("reminder", "")
    reminder = timestamp(edit_time(raw, user["timezone"])) if raw else None

    def apply():
        if reminder and reminder <= timestamp():
            raise DomainError(422, "Выбери будущее время напоминания.")
        bid = identifier()
        db.execute("INSERT INTO budget_entry VALUES (?,?,?,?,?,?,?,?,?,?)",
                   (bid, *scope(user), direction, category, title, amount, day, reminder, timestamp()))
        return bid

    return svc.command(db, user, "budget.add", key, [direction, category, title, amount, day, reminder], apply)


def budget_reminders(db, user, now):
    for row in db.execute("""
        SELECT id,reminder_at FROM budget_entry WHERE university_id=? AND student_id=? AND reminder_at<=?
    """, (*scope(user), now)):
        svc.notify(db, *scope(user), "budget", "", f"budget:{row['id']}:{row['reminder_at']}",
                   "Напоминание о записи бюджета", "Проверь личный бюджет.", now)
