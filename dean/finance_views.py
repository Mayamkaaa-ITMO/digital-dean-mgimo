from . import finances as fin
from .domain import timestamp
from .schedule import today_for
from .ui import back, button, e, empty, form, hidden, input_field, link, local_label, page, panel, select_field


def charge_state(row):
    if row["paid_at"]:
        return "Оплачено в демо"
    return "Просрочено" if row["due_at"] < timestamp() else "К оплате в демо"


def index(request, db):
    user = request.user
    awards = fin.awards(db, user)
    charges = fin.charges(db, user)
    zone = user["timezone"]
    unpaid_total = sum(row["amount"] for row in charges if not row["paid_at"])
    paid_total = sum(row["amount"] for row in charges if row["paid_at"])
    hidden_amounts = fin.amounts_hidden(db, user)
    checked = " checked" if hidden_amounts else ""
    privacy = f'<label class="check"><input type="checkbox" name="private" value="1"{checked}>Скрывать суммы на главной и в уведомлениях</label>'
    body = panel("Суммы и отображение",
                 f'<p>К оплате {e(fin.rubles(unpaid_total))} · Учтено оплат {e(fin.rubles(paid_total))}</p>'
                 + form(request, "/finance/visibility", privacy + button("Сохранить настройку")))
    charge_items = []
    for row in charges:
        meta = f'{charge_state(row)} · {row["period"]} · до {local_label(row["due_at"], zone)}'
        charge_items.append(f'<li><div>{link("/finance/charges/" + row["id"], row["title"])}'
                            f'<p>{e(fin.rubles(row["amount"]))}</p><p class="meta">{e(meta)}</p></div></li>')
    body += panel("Начисления", f'<ul class="rows">{"".join(charge_items)}</ul>' if charge_items else empty("Начислений нет."))
    for key, label in fin.AWARD_KINDS.items():
        items = [
            f'<li><div>{link("/finance/awards/" + row["id"], row["title"])}'
            f'<p>{e(fin.rubles(row["amount"]))} · {e(row["period"])} · '
            f'{"Выплачено в демо" if row["paid_at"] else "Назначено"}</p></div></li>'
            for row in awards if row["category"] == key
        ]
        body += panel(label, f'<ul class="rows">{"".join(items)}</ul>' if items else empty("Записей нет."))
    body += f'<p>{link("/finance/budget", "Личный бюджет")} · {link("/residence", "Проживание")}</p>'
    return page(request, "Финансы", body + back("/student-services", "К сервисам"), "Демонстрационный учёт.")


def charge_detail(request, db, cid):
    user = request.user
    row = fin.charges(db, user, cid)
    zone = user["timezone"]
    href = "/finance/charges/" + cid
    body = f'<p>{e(fin.rubles(row["amount"]))} · {e(row["period"])}</p>'
    body += f'<p>{e(charge_state(row))} · Срок {e(local_label(row["due_at"], zone))}</p>'
    body += f'<p>{link("/services/charge-question?charge=" + cid, "Задать вопрос по начислению")}</p>'
    result = panel("Сведения", body)
    if not row["paid_at"]:
        fields = select_field("result", "Исход симуляции", [("success", "Успех"), ("failed", "Отказ")])
        result += panel("Симуляция оплаты", "<p>Сумма учитывается только в демонстрационных записях.</p>"
                        + form(request, href + "/pay", fields + button("Провести тестовую оплату"), command=True))
    attempts = []
    for item in fin.attempts(db, user, cid):
        label = "Успешно" if item["state"] == "success" else "Неуспешно"
        receipt = link("/finance/receipts/" + item["id"], "Скачать демо-квитанцию") if item["state"] == "success" else ""
        attempts.append(f'<li><div><p>{label} · {e(local_label(item["created_at"], zone))} · '
                        f'{e(fin.rubles(item["amount"]))}</p>{receipt}</div></li>')
    result += panel("Операции", f'<ul class="rows">{"".join(attempts)}</ul>' if attempts else empty("Операций нет."))
    return page(request, row["title"], result + back("/finance", "К финансам"))


def award_detail(request, db, aid, manage=False):
    row = fin.awards(db, request.user, aid, manage=manage)
    zone = request.user["timezone"]
    body = f'<p>{e(fin.rubles(row["amount"]))} · {e(row["period"])}</p>'
    body += f'<p>Назначено {e(local_label(row["assigned_at"], zone))}</p>'
    body += (f'<p>Демо-выплата отмечена {e(local_label(row["paid_at"], zone))}</p>'
             if row["paid_at"] else "<p>Выплата ещё не отмечена.</p>")
    if row["source_app"]:
        body += f'<p>{link("/requests/" + row["source_app"], "Решение по заявке")}</p>'
    if manage and not row["paid_at"]:
        body += form(request, f"/staff/finance/{aid}/pay", button("Отметить демо-выплату"), command=True)
    return page(request, row["title"], panel("История назначения", body)
                + back("/staff/finance" if manage else "/finance", "К списку"))


def staff_index(request, db):
    items = []
    for row in fin.awards(db, request.user, manage=True):
        title = row["full_name"] + " · " + row["title"]
        items.append(f'<li><div>{link("/staff/finance/" + row["id"], title)}'
                     f'<p>{e(fin.rubles(row["amount"]))} · {"Выплачено в демо" if row["paid_at"] else "Назначено"}</p></div></li>')
    body = f'<ul class="rows">{"".join(items)}</ul>' if items else empty("Назначений в доступных группах нет.")
    return page(request, "Назначения и демо-выплаты", panel("Доступные группы", body) + back("/staff", "В кабинет"))


def residence_page(request, db):
    body = ""
    for row in fin.residence(db, request.user):
        action = link("/services/residence-request?residence=" + row["id"], "Подать бытовое обращение")
        info = (f'<p>{e(row["location"])}</p><p>Твоя комната {e(row["room"])}.</p>'
                f'<p>{e(row["starts_on"])} — {e(row["ends_on"])}</p><p>{e(row["rules"])}</p>'
                f'<p>{e(row["contact"])}</p><p>{action}</p>')
        body += panel(row["title"], info)
    return page(request, "Проживание", (body or panel("Моё проживание", empty("Записей о проживании нет.")))
                + back("/student-services", "К сервисам"), "Демонстрационные помещения и контакты.")


def budget_page(request, db):
    rows = fin.budget(db, request.user)
    incoming = sum(row["amount"] for row in rows if row["direction"] == "income")
    outgoing = sum(row["amount"] for row in rows if row["direction"] == "expense")
    entries = []
    for row in rows:
        label = fin.CATEGORIES[row["direction"]][row["category"]]
        prefix = "+" if row["direction"] == "income" else "−"
        entries.append(f'<li><div><strong>{e(row["title"])}</strong><p>{prefix}{e(fin.rubles(row["amount"]))} · '
                       f'{e(label)} · {e(row["entry_date"])}</p></div></li>')
    body = f'<p>Доходы {e(fin.rubles(incoming))} · Расходы {e(fin.rubles(outgoing))}</p>'
    body += f'<ul class="rows">{"".join(entries)}</ul>' if entries else empty("Записей пока нет.")
    forms = []
    for direction, label in (("income", "Добавить доход"), ("expense", "Добавить расход")):
        fields = hidden("direction", direction) + select_field("category", "Категория", fin.CATEGORIES[direction].items())
        fields += input_field("title", "Название", options='maxlength="120" required')
        fields += input_field("amount", "Сумма, ₽", options='inputmode="decimal" maxlength="10" required')
        fields += input_field("day", "Дата", today_for(request.user), "required", "date")
        fields += input_field("reminder", "Напомнить", kind="datetime-local")
        forms.append(panel(label, form(request, "/finance/budget/" + direction, fields + button("Сохранить"), command=True)))
    return page(request, "Личный бюджет", panel("Записи", body) + "".join(forms) + back("/finance", "К финансам"))
