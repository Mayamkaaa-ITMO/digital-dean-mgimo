from . import meetings, team_work, teams, workspace
from .domain import DomainError, timestamp
from .schedule import today_for
from .study_views import date_time
from .ui import back, button, e, empty, form, hidden, input_field, link, linked_row, local_label, page, panel, select_field, textarea

INVITE_STATES = {"pending": "Ожидает ответа", "accepted": "Принято", "declined": "Отклонено",
                 "revoked": "Отозвано", "expired": "Истекло"}


def list_rows(items, fallback):
    return f'<ul class="rows">{"".join(items)}</ul>' if items else empty(fallback)


def index(request, db):
    user = request.user
    items = [f'<li>{link("/teams/" + row["id"], row["title"])}</li>' for row in teams.lists(db, user)]
    incoming = [
        linked_row("/teams/invitations/" + row["id"], row["title"], invite_label(row))
        for row in teams.invitations(db, user)
    ]
    fields = input_field("title", "Название команды", options='maxlength="90" required') + button("Создать команду")
    body = panel("Мои команды", list_rows(items, "Команд пока нет."), link("/clubs", "Каталог клубов"))
    body += panel("Приглашения", list_rows(incoming, "Приглашений пока нет."))
    body += panel("Новая проектная команда", form(request, "/teams", fields, command=True))
    return page(request, "Совместная учёба", body + back("/student-services", "К сервисам"))


def invite_label(row):
    if row["status"] == "pending" and row["expires_at"] <= timestamp():
        return INVITE_STATES["expired"]
    return INVITE_STATES[row["status"]]


def invitation(request, db, iid):
    row = teams.invitation(db, request.user, iid)
    body = f'<p>{e(invite_label(row))}</p><p>Срок {e(local_label(row["expires_at"], request.user["timezone"]))}.</p>'
    if row["status"] == "pending" and row["expires_at"] > timestamp():
        for action, label in (("accepted", "Принять"), ("declined", "Отклонить")):
            body += form(request, f"/teams/invitations/{iid}/{action}", button(label), command=True)
    return page(request, "Приглашение · " + row["title"], panel("Приглашение", body) + back("/teams", "К командам"))


def member_controls(request, db, parent):
    user = request.user
    tid = parent["id"]
    owner = parent["owner_id"] == user["id"] and parent["kind"] == "project"
    people = []
    for row in teams.members(db, parent):
        control = ""
        if owner and row["id"] != user["id"]:
            control = form(request, f'/teams/{tid}/remove/{row["id"]}', button("Удалить из команды"), command=True)
        people.append(f'<li><div>{e(row["full_name"])}{control}</div></li>')
    body = list_rows(people, "Участников пока нет.")
    if owner:
        fields = input_field("login", "Логин студента", options='maxlength="64" required')
        body += form(request, f"/teams/{tid}/invite", fields + button("Пригласить"), command=True)
        for row in db.execute("""
            SELECT i.*,a.full_name FROM team_invitation i JOIN account a ON a.id=i.student_id
            WHERE i.team_id=? AND i.status='pending' AND i.expires_at>? ORDER BY i.rowid
        """, (tid, timestamp())):
            body += f'<p>{e(row["full_name"])} · приглашение отправлено.</p>'
            body += form(request, f'/teams/{tid}/invites/{row["id"]}/revoke', button("Отозвать приглашение"), command=True)
    return panel("Участники", body)


def team_page(request, db, tid):
    user = request.user
    parent = teams.team(db, user, tid)
    body = member_controls(request, db, parent)
    if parent["kind"] == "project":
        task_rows = []
        for row in team_work.tasks(db, user, tid):
            state = "Готово" if row["state"] == "done" else "В работе"
            deadline = f' · {local_label(row["due_at"], user["timezone"])}' if row["due_at"] else ""
            meta = f'{state} · {row["assignee"] or "Без ответственного"}{deadline}'
            task_rows.append(f'<li><div>{link(f"/teams/{tid}/tasks/{row["id"]}", row["title"])}'
                             f'<p>{e(meta)}</p></div></li>')
        body += panel("Общие задачи", list_rows(task_rows, "Задач пока нет.")
                      + '<p class="meta">Срок общей задачи отображается в расписании ответственного.</p>',
                      link(f"/teams/{tid}/tasks/new", "Добавить общую задачу"))
        shown = []
        for row in team_work.shared_notes(db, user, tid):
            path = f'/teams/{tid}/notes/{row["id"]}'
            shown.append(f'<li><div>{link(path, row["body"][:80])}<p>{e(row["full_name"])}</p></div></li>')
        selection = [(n["id"], n["body"][:66]) for n in team_work.shareable_notes(db, user)]
        notes = list_rows(shown, "Участники пока ничего не открыли.")
        if selection:
            notes += form(request, f"/teams/{tid}/share-note", select_field("note", "Выбери личную заметку", selection)
                          + button("Открыть заметку команде"), command=True)
        body += panel("Общие заметки", notes)
        allow = bool(parent["allow_availability"])
        consent_label = "Отозвать доступ к занятости" if allow else "Разрешить проверку занятости"
        preferences = form(request, f"/teams/{tid}/consent", hidden("allow", int(not allow))
                           + button(consent_label), command=True)
        preferences += '<p>Участники с согласием видят только общие свободные интервалы.</p>'
        body += panel("Совместное время", preferences, link(f"/teams/{tid}/free", "Найти время"))
    events = []
    for row in meetings.upcoming(db, user, tid):
        detail = "Отменена" if row["cancelled"] else meetings.STATES[row["status"]]
        events.append(f'<li><div>{link("/teams/meetings/" + row["id"], row["title"])}'
                      f'<p>{e(local_label(row["starts_at"], user["timezone"]))} · {e(detail)}</p></div></li>')
    body += panel("Встречи", list_rows(events, "Встреч пока нет."))
    body += discussion(request, db, parent)
    return page(request, parent["title"], body + back("/teams", "К командам"))


def task_form(request, db, tid, task_id=None):
    user = request.user
    row = team_work.tasks(db, user, tid, task_id, manage=True) if task_id else {}
    parent = teams.team(db, user, tid)
    options = [("", "Без ответственного"), *((a["id"], a["full_name"]) for a in teams.members(db, parent))]
    fields = input_field("title", "Название", row["title"] if row else "", 'required maxlength="120"')
    fields += textarea("description", "Описание", 1600, value=row["description"] if row else "")
    fields += select_field("assignee", "Ответственный", options, row["assignee_id"] if row else "")
    fields += input_field("due", "Срок", date_time(row["due_at"], user["timezone"]) if row else "", kind="datetime-local")
    fields += '<p class="meta">Чтобы срок появился в календаре, выбери ответственного.</p>'
    fields += select_field("priority", "Приоритет", workspace.PRIORITIES.items(), row["priority"] if row else "normal")
    if row:
        fields += hidden("version", row["version"])
    route = f"/teams/{tid}/tasks/" + (f"{task_id}/edit" if task_id else "new")
    return page(request, "Правка общей задачи" if row else "Новая общая задача",
                panel("Параметры", form(request, route, fields + button("Сохранить"), command=True))
                + back(f"/teams/{tid}", "К команде"))


def task_detail(request, db, tid, task_id):
    user = request.user
    parent = teams.team(db, user, tid)
    row = team_work.tasks(db, user, tid, task_id)
    route = f"/teams/{tid}/tasks/{task_id}"
    detail = f'<p>{e(row["assignee"] or "Без ответственного")} · {"Готово" if row["state"] == "done" else "В работе"}</p>'
    detail += f'<p class="multiline">{e(row["description"])}</p>'
    if row["due_at"]:
        detail += f'<p>Срок {e(local_label(row["due_at"], user["timezone"]))}.</p>'
        if not row["assignee_id"]:
            detail += '<p class="meta">Выбери ответственного, чтобы срок появился в его расписании.</p>'
    allowed = user["id"] in {row["owner_id"], parent["owner_id"], row["assignee_id"]}
    if allowed:
        state, label = ("todo", "Открыть заново") if row["state"] == "done" else ("done", "Завершить")
        detail += form(request, route + "/" + state, hidden("version", row["version"]) + button(label), command=True)
        detail += f'<p>{link(route + "/edit", "Изменить")}</p>'
    body = panel("Задача", detail)
    subtasks = []
    for task in db.execute("SELECT * FROM subtask WHERE task_id=? ORDER BY rowid", (task_id,)):
        line = f'<p>{e(task["title"])} · {"Готово" if task["done"] else "В работе"}</p>'
        if allowed:
            action, label = ("reopen", "Открыть") if task["done"] else ("complete", "Отметить готовым")
            line += form(request, f'{route}/subtasks/{task["id"]}/{action}', button(label), command=True)
        subtasks.append(f"<li><div>{line}</div></li>")
    text = list_rows(subtasks, "Подзадач пока нет.")
    if allowed:
        fields = input_field("title", "Название подзадачи", options='required maxlength="120"')
        text += form(request, route + "/subtasks", fields + button("Добавить"), command=True)
    body += panel(f'Подзадачи · {row["done"]} из {row["total"]}', text)
    return page(request, row["title"], body + discussion(request, db, parent, task_id) + back(f"/teams/{tid}", "К команде"))


def note_detail(request, db, tid, nid):
    row = team_work.shared_notes(db, request.user, tid, nid)
    body = f'<p class="multiline">{e(row["body"])}</p><p class="meta">{e(row["full_name"])}</p>'
    if row["owner_id"] == request.user["id"]:
        body += form(request, f"/teams/{tid}/notes/{nid}/revoke", button("Закрыть доступ команде"), command=True)
    return page(request, "Общая заметка", panel("Заметка", body) + back(f"/teams/{tid}", "К команде"))


def discussion(request, db, parent, task_id=None):
    tid = parent["id"]
    if not parent["moderator_id"]:
        return panel("Обсуждение", empty("Обсуждение недоступно без назначенного модератора."))
    lines = []
    for message in team_work.post_list(db, request.user, tid, task_id):
        meta = f'{message["full_name"]} · {local_label(message["created_at"], request.user["timezone"])}'
        report = ""
        if message["author_id"] != request.user["id"]:
            report = f'<details><summary>Пожаловаться</summary>' + form(request, f'/teams/{tid}/report/{message["id"]}',
                     input_field("reason", "Причина", options='maxlength="300" required') + button("Отправить жалобу"),
                     command=True) + "</details>"
        lines.append(f'<li><div><p class="meta">{e(meta)}</p><p class="multiline">{e(message["body"])}</p>{report}</div></li>')
    path = f"/teams/{tid}/tasks/{task_id}/post" if task_id else f"/teams/{tid}/post"
    body = list_rows(lines, "Сообщений пока нет.")
    body += form(request, path, textarea("body", "Сообщение", 1000, required=True) + button("Отправить"), command=True)
    return panel("Обсуждение", body)


def moderation(request, db):
    rows = team_work.moderation(db, request.user)
    body = ""
    for row in rows:
        detail = f'<p class="multiline">{e(row["body"])}</p><p>Жалоба: {e(row["reason"])}</p>'
        if row["resolved_at"]:
            detail += f'<p>{e(row["decision"])}</p>'
        else:
            fields = select_field("outcome", "Действие", [("hidden", "Скрыть сообщение"), ("dismissed", "Отклонить жалобу")])
            fields += input_field("decision", "Причина решения", options='required maxlength="500"')
            detail += form(request, f'/staff/moderation/{row["id"]}', fields + button("Сохранить решение"), command=True)
        body += panel(row["title"], detail)
    return page(request, "Жалобы на сообщения", (body or empty("Жалоб пока нет.")) + back("/staff", "В кабинет"))


def free(request, db, tid):
    user = request.user
    parent = teams.team(db, user, tid)
    initial = today_for(user).isoformat()
    first, last, minutes = (request.query("from", initial), request.query("to", initial), request.query("minutes", "60"))
    fields = input_field("from", "С даты", first, "required", "date")
    fields += input_field("to", "По дату", last, "required", "date")
    fields += select_field("minutes", "Длительность", [(str(n), f"{n} мин.") for n in (30, 60, 90, 120)], minutes)
    body = f'<form method="get" action="/teams/{tid}/free">{fields}{button("Найти свободное время")}</form>'
    count = len(meetings.consenting_members(db, parent))
    requirement = "<p>Подбор доступен, когда ты и хотя бы один участник разрешили проверку занятости.</p>"
    if not parent["allow_availability"]:
        consent = hidden("allow", 1) + hidden("return_to_picker", "1")
        if request.query("from"):
            consent += hidden("from", first) + hidden("to", last) + hidden("minutes", minutes)
        body = form(request, f"/teams/{tid}/consent", consent + button("Разрешить проверку занятости"),
                    command=True)
    details = f'{requirement}<p>Участников с согласием: {count}.</p>'
    status = 200
    if request.query("from"):
        try:
            _, options = meetings.suggestions(db, user, tid, first, last, minutes)
        except DomainError as error:
            if error.status != 422:
                raise
            status = 422
            details += f'<p class="change" role="alert">{e(error.message)}</p>'
            options = []
        for start, end in options:
            when = f'{local_label(start, user["timezone"])} — {local_label(end, user["timezone"])}'
            fields = hidden("start", date_time(start, user["timezone"])) + hidden("end", date_time(end, user["timezone"]))
            fields += input_field("title", "Название", "Совместная подготовка", 'required maxlength="100"')
            details += f'<p><strong>{e(when)}</strong></p>' + form(request, f"/teams/{tid}/meetings", fields
                        + button("Пригласить на это время"), command=True)
        if not options and status == 200:
            details += empty("В выбранном диапазоне нет общего свободного интервала.")
    html = page(request, "Подбор времени", back(f"/teams/{tid}", "К команде") + panel(parent["title"], body)
                + panel("Свободные интервалы", details))
    return html if status == 200 else (status, html, [])


def meeting_detail(request, db, mid):
    user, zone = request.user, request.user["timezone"]
    row = meetings.meeting(db, user, mid)
    status = "Отменена" if row["cancelled"] else meetings.STATES[row["status"]]
    body = f'<p>{e(local_label(row["starts_at"], zone))} — {e(local_label(row["ends_at"], zone))}</p><p>{e(status)}</p>'
    active = not row["cancelled"] and row["starts_at"] > timestamp()
    version = hidden("version", row["version"])
    prefix = f"/teams/meetings/{mid}"
    if active and row["organizer_id"] == user["id"]:
        inputs = version + input_field("title", "Название", row["title"], 'maxlength="100" required')
        inputs += input_field("start", "Начало", date_time(row["starts_at"], zone), "required", "datetime-local")
        inputs += input_field("end", "Окончание", date_time(row["ends_at"], zone), "required", "datetime-local")
        body += form(request, prefix + "/move", inputs + button("Перенести и запросить ответы"), command=True)
        body += form(request, prefix + "/cancel", version + button("Отменить встречу"), command=True)
    elif active:
        for action, label in (("accepted", "Принять"), ("declined", "Отклонить")):
            if row["status"] != action:
                body += form(request, prefix + "/" + action, version + button(label), command=True)
    return page(request, row["title"], panel(row["team_name"], body) + back(f'/teams/{row["team_id"]}', "К команде"))


def clubs(request, db):
    user = request.user
    joined = {row["id"] for row in teams.lists(db, user)}
    body = ""
    for row in teams.club_catalog(db, user):
        cid = row["id"]
        action = link(f"/teams/{cid}", "Открыть клуб") if cid in joined else link(f"/services/club-join?club={cid}", "Подать заявку")
        body += panel(row["title"], f'<p>{e(row["description"])}</p><p>{action}</p>')
    return page(request, "Клубы", (body or empty("Клубов пока нет.")) + back("/teams", "К командам"))
