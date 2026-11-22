import json

from . import attendance, individual_plan, reservations, retakes
from .domain import timestamp
from .reservation_views import retake_admission, retake_availability, retake_badge
from .ui import back, button, e, empty, form, hidden, input_field, link, local_label, page, panel, select_field, textarea


def debts_page(request, db, manage=False):
    path = "/staff/retakes" if manage else "/retakes"
    rows = []
    for debt in retakes.debts(db, request.user, manage=manage):
        status, next_step = retake_admission(debt)
        title = debt["subject"] + (" · " + debt["full_name"] if manage else "")
        hint = "" if manage else f'<p class="meta">{e(next_step)}</p>'
        rows.append(f'<li><div>{link(path + "/" + debt["id"], title)}{retake_badge(debt, status)}{hint}</div></li>')
    listing = f'<ul class="rows">{"".join(rows)}</ul>' if rows else empty("Подтверждённых задолженностей нет.")
    return page(request, "Задолженности и пересдачи", panel("Задолженности", listing)
                + back("/staff" if manage else "/grades", "Назад"))


def debt_detail(request, db, did, manage=False):
    user = request.user
    zone = user["timezone"]
    row = retakes.debts(db, user, did, manage=manage)
    state, next_step = retake_admission(row)
    body = retake_badge(row, state) + f'<p>{e(row["reason"])}</p><p>{e(row["conditions"])}</p>'
    if not manage:
        body += f'<p>{e(next_step)}</p>'
        if row["closed_at"]:
            body += link("#retake-history", "Посмотреть результат", "ux-flow-link")
        elif not row["admitted"]:
            body += link("/notifications", "К уведомлениям", "ux-flow-link")
        else:
            body += link("#retake-slots", "К выбору времени", "ux-flow-link")
    if manage:
        body += f'<p>{e(row["full_name"])}</p>'
        if not row["closed_at"]:
            action = "revoke" if row["admitted"] else "admit"
            label = "Отозвать допуск" if row["admitted"] else "Подтвердить допуск"
            body += form(request, f"/staff/retakes/{did}/{action}",
                         textarea("reason", "Комментарий к допуску", 300, required=True) + button(label), command=True)
    parts = panel("Задолженность", body)
    options = []
    windows = []
    for offer in retakes.offers(db, row):
        if manage:
            if offer["manager_id"] == user["id"]:
                options.append(f'<p>{link("/staff/bookables/" + offer["id"], "Управлять окнами")}</p>')
        elif not row["closed_at"]:
            for slot in reservations.slots(db, user, offer["id"]):
                if not slot["closed"] and slot["starts_at"] > timestamp():
                    available, label = retake_availability(db, user, row, slot, reservations.occupied(db, slot))
                    tone = "good" if available else "neutral"
                    windows.append(f'<li><div>{link("/slots/" + slot["id"], local_label(slot["starts_at"], zone))}'
                                   f'<p class="meta">{e(slot["place"])}</p></div>'
                                   f'<span class="tag" data-tone="{tone}">{e(label)}</span></li>')
    if manage and not options and not row["closed_at"]:
        options.append(form(request, f"/staff/retakes/{did}/offer",
                            input_field("place", "Место пересдачи", options='maxlength="120" required')
                            + button("Создать ресурс пересдачи"), command=True))
    if not manage and windows:
        options.append(f'<ul class="rows results">{"".join(windows)}</ul>')
    if manage or not row["closed_at"]:
        parts += panel("Окна пересдачи", "".join(options) or empty("Предстоящих окон нет."), key="retake-slots")
    attempts = []
    for item in retakes.attempts(db, row):
        label = ("Отменена" if item["status"] == "cancelled" else
                 retakes.RESULTS.get(item["result"], "Запись подтверждена"))
        detail = f'<p>{e(local_label(item["starts_at"], zone))} · {e(label)}</p>'
        if item["reason"]:
            detail += f'<p>{e(item["reason"])}</p>'
        rid = item["reservation_id"]
        if not manage:
            detail += f'<p>{link("/bookings/" + rid, "Карточка записи")}</p>'
        elif item["status"] == "reserved" and not item["result"] and item["ends_at"] <= timestamp():
            fields = hidden("reservation", rid) + select_field("result", "Результат", retakes.RESULTS.items())
            fields += textarea("reason", "Комментарий", 500, required=True) + button("Зафиксировать результат")
            detail += form(request, f"/staff/retakes/{did}/result", fields, command=True)
        attempts.append(f'<li><div>{detail}</div></li>')
    parts += panel("История записей и результатов", f'<ul class="rows">{"".join(attempts)}</ul>' if attempts else
                   empty("Записей нет."), key="retake-history")
    return page(request, row["subject"], parts + back("/staff/retakes" if manage else "/retakes", "К задолженностям"))


def curriculum_panels(request, db):
    user = request.user
    content = ""
    for block in individual_plan.blocks(db, user):
        if block["rule"] != "credits":
            continue
        courses = individual_plan.courses(db, user, block)
        choices = []
        for row in courses:
            checked = " checked" if row["selected"] else ""
            available = individual_plan.eligible(db, user, row)
            description = f'{row["title"]} · {row["credits"]} кр.'
            choices.append(f'<label class="check"><input type="checkbox" name="item_{row["id"]}" '
                           f'value="1"{checked}>{e(description)}</label>')
            choices.append(f'<p class="meta">{link("/curriculum/course/" + row["id"], "О дисциплине")} · '
                           f'Мест: {row["capacity"] - row["enrolled"]} · '
                           f'{e("Требование выполнено" if available else "Требование не выполнено")}</p>')
        window = f'Окно до {local_label(courses[0]["window_end"], user["timezone"])}.' if courses else ""
        caption = f'<p>Выбери ровно {block["credit_target"]} кредита. {e(window)}</p>'
        content += panel(block["title"], caption + form(request, f'/curriculum/block/{block["id"]}',
                         "".join(choices) + button("Сохранить личный выбор"), command=True))
    approved = individual_plan.approved(db, user)
    plans = []
    for row in approved:
        titles = ", ".join(item["title"] for block in json.loads(row["snapshot_json"]) for item in block["items"])
        plans.append(f'<li><div>{link("/requests/" + row["id"], local_label(row["approved_at"], user["timezone"]))}'
                     f'<p>{e(titles)}</p></div></li>')
    content += panel("Утверждённые индивидуальные планы", f'<ul class="rows">{"".join(plans)}</ul>' if plans else
                     empty("Утверждённых версий пока нет."), link("/services/individual-plan", "Подать свой выбор на рассмотрение"))
    return content


def course_page(request, db, iid):
    row = individual_plan.course(db, request.user, iid)
    info = (f'<p>{row["credits"]} кр. · {row["hours"]} академических часов</p><p>{e(row["topics"])}</p>'
            f'<p>Контроль: {e(row["assessment"])}</p><p>Входное требование: {e(row["prerequisite"] or "Нет")}.</p>')
    return page(request, row["title"], panel("О дисциплине", info) + back("/curriculum", "К учебному плану"))


def attendance_page(request, db, eid):
    zone = request.user["timezone"]
    people = attendance.roster(db, request.user, eid)
    body = ""
    for student in people:
        student_id = student["id"]
        inputs = hidden("student", student_id) + select_field("state", "Отметка", attendance.STATES.items(), student["state"])
        inputs += input_field("reason", "Причина исправления", options='maxlength="300"')
        history = ""
        for row in db.execute("""
            SELECT h.*,a.full_name FROM lesson_attendance_history h JOIN account a ON a.id=h.actor_id
            WHERE h.event_id=? AND h.student_id=? ORDER BY h.id DESC
        """, (eid, student_id)):
            line = f'{attendance.STATES[row["state"]]} · {row["full_name"]} · {local_label(row["changed_at"], zone)}'
            history += f'<li>{e(line)}<p>{e(row["reason"])}</p></li>'
        body += panel(student["full_name"], form(request, f"/staff/attendance/{eid}", inputs + button("Сохранить отметку"),
                      command=True) + (f'<ol class="timeline">{history}</ol>' if history else ""))
    return page(request, "Посещаемость занятия", body + back(f"/staff/schedule/{eid}", "К занятию"))
