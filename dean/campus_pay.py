"""Student-scoped simulated purchases, served only on an opted-in demo tenant."""
from base64 import b64encode
from os import getenv

from . import services, workspace
from .domain import DomainError, identifier, require, timestamp
from .event_tickets import DEMO, png
from .ui import back, button, e, empty, form, hidden, input_field, link, local_label, page, panel

PATH = "/campus-pay"
TERMINAL = PATH + "/terminal"
LABEL = "Campus Pay"
INITIAL = 300
CATALOG = {
    "tea": ("Чай в учебном кафе", 35),
    "lunch": ("Обед в учебном кафе", 110),
    "kit": ("Набор для учебного проекта", 215),
}
STATES = {"pending": "Ожидает QR-подтверждения", "paid": "Подтверждена",
          "refunded": "Баллы возвращены", "cancelled": "Отменена"}


def installed(user):
    return getenv("DEAN_EXT_CAMPUS_PAY") == "1" and (user["university_id"] == DEMO or getenv("DEAN_PRODUCT") == "1")


def available(user):
    return user["role"] == "student" and installed(user)


def scope(user):
    if not available(user):
        raise DomainError(404, "Демонстрационная оплата недоступна.")
    return workspace.scope(user)


def amount(db, owner):
    spent = db.execute("""
        SELECT coalesce(sum(cost),0) FROM campus_demo_purchase WHERE university_id=? AND owner_id=? AND state='paid'
    """, owner).fetchone()[0]
    return INITIAL - spent


def purchase(db, owner, item):
    return require(db.execute("SELECT * FROM campus_demo_purchase WHERE university_id=? AND owner_id=? AND id=?",
                              (*owner, item)).fetchone())


def code(item):
    return "DEAN-DEMO-" + item.upper()


def checkout(db, user, raw, key):
    owner = scope(user)
    if raw not in CATALOG:
        raise DomainError(422, "Выбери товар из учебного каталога.")
    label, cost = CATALOG[raw]

    def apply():
        item = identifier()
        db.execute("""
            INSERT INTO campus_demo_purchase (id,university_id,owner_id,product,cost,state,created_at)
            VALUES (?,?,?,?,?,'pending',?)
        """, (item, *owner, label, cost, timestamp()))
        return item

    return services.command(db, user, "campus-pay.checkout", key, [raw], apply)


def change(db, user, item, action, confirmation, key, *, method="qr"):
    owner = scope(user)

    def apply():
        row = purchase(db, owner, item)
        state, stamp = row["state"], timestamp()
        if action == "confirm":
            if code(item) != confirmation.strip().upper():
                raise DomainError(422, "Проверь код из QR текущей операции.")
            if state == "paid":
                return item
            if state != "pending":
                raise DomainError(409, "Операция уже закрыта. Создай новую покупку.")
            if amount(db, owner) < row["cost"]:
                raise DomainError(409, "Демонстрационных баллов не хватает.")
            db.execute("UPDATE campus_demo_purchase SET state='paid',paid_at=? WHERE id=?", (stamp, item))
            db.execute("INSERT INTO lab_purchase_method VALUES (?,?)",(item,method))
        elif state in {"pending", "paid"}:
            target = "cancelled" if state == "pending" else "refunded"
            db.execute("UPDATE campus_demo_purchase SET state=?,closed_at=? WHERE id=?", (target, stamp, item))
        return item

    if action not in {"confirm", "cancel"}:
        raise DomainError(422, "Выбери действие с покупкой.")
    payload = [item, confirmation] if method == "qr" else [item, confirmation, method]
    return services.command(db, user, "campus-pay." + action, key, payload, apply)


def status_label(state):
    if state == "pending" and getenv("DEAN_CAMPUS_LAB") == "1":
        return "Ожидает подтверждения на кассе"
    return STATES[state]


def balance_panel(db, owner):
    total = amount(db, owner)
    return panel("Баланс", f"<p><strong>{total} демо-баллов</strong></p><p>Стартовый учебный баланс — {INITIAL} баллов.</p>")


def index(request, db):
    owner = scope(request.user)
    catalog = ""
    for key, (name, price) in CATALOG.items():
        action = form(request, PATH, hidden("product", key) + button("Купить"), command=True)
        catalog += f'<li><div><strong>{e(name)}</strong><p>{price} демо-баллов</p>{action}</div></li>'
    history = ""
    for row in db.execute("""
        SELECT * FROM campus_demo_purchase WHERE university_id=? AND owner_id=? ORDER BY created_at DESC,rowid DESC
    """, owner):
        when = local_label(row["created_at"], request.user["timezone"])
        history += f'<li><div>{link(PATH + "/" + row["id"], row["product"])}'
        history += f'<p class="meta">{e(when)} · {e(status_label(row["state"]))} · {row["cost"]} демо-баллов</p></div></li>'
    return page(request, LABEL, balance_panel(db, owner)
                + panel("Учебный каталог", f'<ul class="rows">{catalog}</ul>')
                + panel("История операций", f'<ul class="rows">{history}</ul>' if history else empty("Покупок пока нет.")),
                "Учебные покупки за демо-баллы")


def terminal(request, db):
    from .campus_lab import access

    owner = scope(request.user)
    access(request.user)
    pending = db.execute("""
        SELECT id,product,cost FROM campus_demo_purchase
        WHERE university_id=? AND owner_id=? AND state='pending'
        ORDER BY created_at DESC,rowid DESC LIMIT 20
    """, owner)
    items = []
    for item in pending:
        action = PATH + "/" + item["id"] + "/confirm"
        confirm = form(request, action, hidden("code", code(item["id"]))
                       + button(f'Подтвердить за {item["cost"]} демо-баллов'), command=True)
        items.append(f'<li><div><strong>{e(item["product"])}</strong>{confirm}</div></li>')
    orders = ("<ul class=\"rows\">" + "".join(items) + "</ul>" if items
              else empty("Нет ожидающих покупок.") + link(PATH, "Выбрать в учебном каталоге"))
    return page(request, "Учебная касса", back(PATH, "К кошельку") + balance_panel(db, owner)
                + panel("Подтверждение покупки", orders), "Покупки за учебные баллы")


def detail(request, db, item, *, error=""):
    owner = scope(request.user)
    row = purchase(db, owner, item)
    path = PATH + "/" + item
    state = row["state"]
    text = f'<p>{e(status_label(state))} · {row["cost"]} демо-баллов.</p>'
    receipt = db.execute("SELECT method FROM lab_purchase_method WHERE purchase_id=?", (item,)).fetchone()
    if receipt:
        label = {"nfc":"NFC-метка", "demo":"Демо касания", "qr":"Код покупки"}[receipt[0]]
        text += f'<p>Способ подтверждения · {label}</p>'
    if error:
        text += f'<p class="change" role="alert" tabindex="-1" data-restore-focus>{e(error)}</p>'
    if state == "pending":
        token = code(item)
        if getenv("DEAN_CAMPUS_LAB") == "1":
            from .campus_lab import TAG
            text += (f'<section data-nfc-pay data-tag="{TAG}"><h3>Оплата касанием</h3>'
                     '<p>Считай метку учебной кассы и подтверди показанную сумму.</p>'
                     '<button type="button" data-nfc-read>Считать NFC-метку</button> '
                     '<button type="button" data-nfc-sim>Демо касания без метки</button>'
                     f'<p>{link(TERMINAL, "Открыть учебную кассу")}</p>'
                     '<p role="status" data-nfc-status></p>' + form(request, path+"/tap",
                     hidden("terminal","") + hidden("mode","") + '<button data-nfc-confirm disabled>Подтвердить оплату баллами</button>',
                     command=True) + '</section>')
        data = b64encode(png(token)).decode("ascii")
        text += f'<img src="data:image/png;base64,{data}" alt="QR демонстрационной оплаты" width="205" height="205">'
        text += input_field("shown_code", "Код из QR", token, "readonly")
        text += form(request, path+"/confirm",
                     input_field("code", "Код подтверждения", options='required maxlength="42" autocomplete="off"')
                     + button("Подтвердить демо-покупку"), command=True)
    if state in {"pending", "paid"}:
        label = "Отменить покупку" if state == "pending" else "Вернуть демо-баллы"
        text += form(request, path+"/cancel", button(label), command=True)
    return page(request, LABEL, back(PATH, "К кошельку") + balance_panel(db, owner) + panel(row["product"], text))

def tap(request, db, item):
    from .campus_lab import TAG, access
    access(request.user)
    if request.field("terminal") != TAG or request.field("mode") not in {"nfc","demo"}:
        raise DomainError(422, "Нужна метка учебной кассы.")
    change(db, request.user, item, "confirm", code(item), request.field("request_id"), method=request.field("mode"))
    return 303, b"", [("Location", PATH + "/" + item)]
