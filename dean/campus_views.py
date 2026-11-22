from . import equipment, library_queue
from .domain import timestamp
from .ui import back, button, e, empty, form, input_field, link, local_label, page, panel, textarea


def equipment_page(request, db, manage=False):
    user = request.user
    prefix = "/staff/equipment" if manage else "/equipment"
    body = ""
    for row in equipment.units(db, user, manage=manage):
        status = "На обслуживании" if row["maintenance"] else "Можно выбрать срок"
        detail = f'<p>{e(row["label"])} · {e(status)} · Действующих записей {row["active"]}.</p>'
        if manage:
            action = "enable" if not row["maintenance"] else "disable"
            fields = input_field("reason", "Причина", options='required maxlength="300"')
            label = "Начать обслуживание" if action == "enable" else "Вернуть в каталог"
            detail += form(request, f'{prefix}/{row["id"]}/maintenance/{action}', fields + button(label), command=True)
        else:
            detail += f'<p>{link(prefix + "/" + row["id"], "Карточка и резерв")}</p>'
        body += panel(row["title"], detail)
    records = []
    for row in equipment.loans(db, user, manage=manage):
        title = row["title"] + (" · " + row["full_name"] if manage else "")
        records.append(f'<li><div>{link(prefix + "/loans/" + row["id"], title)}'
                       f'<p>{e(equipment.STATES[row["state"]])} · {e(local_label(row["starts_at"], user["timezone"]))}</p></div></li>')
    body += panel("Резервы и выдачи", f'<ul class="rows">{"".join(records)}</ul>' if records else empty("Записей нет."))
    return page(request, "Техника кампуса", body + back("/staff" if manage else "/student-services", "Назад"))


def unit_detail(request, db, uid):
    row = equipment.units(db, request.user, uid)
    info = f'<p>Инвентарная единица {e(row["label"])}</p><p>Получение: {e(row["pickup"])}</p><p>Возврат: {e(row["return_place"])}</p>'
    if row["maintenance"]:
        info += empty("Единица на обслуживании.")
    else:
        fields = input_field("starts", "Начало резерва", options="required", kind="datetime-local")
        fields += input_field("ends", "Конец резерва", options="required", kind="datetime-local")
        fields += textarea("purpose", "Цель", 500, required=True)
        fields += input_field("software", "Нужное ПО", options='maxlength="250"')
        info += form(request, f"/equipment/{uid}/reserve", fields + button("Зарезервировать"), command=True)
    return page(request, row["title"], panel("Выдача и резерв", info) + back("/equipment", "К технике"))


def equipment_loan(request, db, lid, manage=False):
    user, zone = request.user, request.user["timezone"]
    row = equipment.loans(db, user, lid, manage=manage)
    prefix = "/staff/equipment" if manage else "/equipment"
    state = row["state"]
    info = (f'<p>{e(equipment.STATES[state])} · {e(row["label"])}</p>'
            f'<p>{e(local_label(row["starts_at"], zone))} — {e(local_label(row["ends_at"], zone))}</p>'
            f'<p>Цель: {e(row["purpose"])}</p><p>ПО: {e(row["software"] or "Без требований")}</p>'
            f'<p>Получение: {e(row["pickup"])}</p><p>Возврат: {e(row["return_place"])}</p>')
    if manage:
        info += f'<p>Студент: {e(row["full_name"])}</p>'
    action = ""
    if state == "reserved" and not manage:
        action, label = "cancel", "Отменить резерв"
    elif state == "reserved" and row["starts_at"] <= timestamp() < row["ends_at"]:
        action, label = "issue", "Подтвердить выдачу"
    elif state == "issued" and manage:
        action, label = "return", "Подтвердить возврат"
    if action:
        fields = input_field("note", "Комментарий", options='maxlength="300"') + button(label)
        info += form(request, f"{prefix}/loans/{lid}/{action}", fields, command=True)
    events = []
    for item in equipment.history(db, row):
        meta = f'{item["full_name"]} · {local_label(item["occurred_at"], zone)}'
        events.append(f'<li><strong>{e(equipment.STATES[item["action"]])}</strong><p class="meta">{e(meta)}</p>'
                      f'<p>{e(item["note"])}</p></li>')
    return page(request, row["title"], panel("Карточка выдачи", info)
                + panel("История", f'<ol class="timeline">{"".join(events)}</ol>') + back(prefix, "К списку"))


def hold_detail(request, db, hid):
    library_queue.refresh(db, request.user)
    row = library_queue.holds(db, request.user, hid)
    body = f'<p>{e(library_queue.STATES[row["state"]])}</p>'
    if row["expires_at"]:
        body += f'<p>Экземпляр {e(row["label"])} · срок {e(local_label(row["expires_at"], request.user["timezone"]))}.</p>'
    if row["state"] in {"waiting", "offered"}:
        body += form(request, f"/library/holds/{hid}/cancel", button("Отменить резерв"), command=True)
    return page(request, row["title"], panel("Резерв книги", body) + back("/library/mine", "К моим книгам"))
