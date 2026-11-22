import json
from datetime import date, datetime, timedelta

from . import coursework, coursework_views, learning, lecture_channel, services as svc, workspace
from .clock import now
from .domain import DomainError, timestamp
from .schedule import local_value, today_for
from .ui import (back, button, e, empty, form, hidden, input_field, link, local_label,
                 page, panel, select_field, textarea)


def date_time(value, zone):
    return local_value(value, zone).strftime("%Y-%m-%dT%H:%M") if value else ""


def task_rows(rows, zone):
    items = []
    for row in rows:
        title = link(f"/tasks/{row['id']}", row["title"])
        due = local_label(row["due_at"], zone) if row["due_at"] else "Без срока"
        details = [row["subject"], f"Срок: {due}", f"Приоритет: {workspace.PRIORITIES[row['priority']]}"]
        if "total" in row.keys() and row["total"]:
            details.append(f'Подзадачи: {row["done"]}/{row["total"]}')
        label = "Готово" if row["state"] == "done" else "В работе"
        items.append(f'<li><div>{title}<p class="meta">{e(" · ".join(filter(None, details)))}</p></div>'
                     f'<span class="tag">{label}</span></li>')
    return f'<ul class="rows results">{"".join(items)}</ul>' if items else empty("Подходящих задач нет.")


def subject_choices(db, user):
    return [("", "Без дисциплины"), *((row["id"], row["name"]) for row in workspace.subjects(db, user))]


def tasks(request, db):
    user = request.user
    q, state, priority, subject = [request.query(name) for name in ("q", "state", "priority", "subject")]
    filters = input_field("q", "Поиск задачи", q[:120], 'maxlength="120"', "search")
    filters += select_field("state", "Статус", [("", "Все"), ("todo", "В работе"), ("done", "Готово")], state)
    filters += select_field("priority", "Приоритет", [("", "Все"), *workspace.PRIORITIES.items()], priority)
    subjects = subject_choices(db, user)
    subjects[0] = ("", "Все дисциплины")
    filters += select_field("subject", "Дисциплина", subjects, subject)
    rows = workspace.tasks(db, user, state=state, priority=priority, subject=subject, text=q)
    body = f'<form method="get" action="/tasks" class="inline">{filters}{button("Найти")}</form>'
    if rows:
        results = task_rows(rows, user["timezone"])
    elif any((q, state, priority, subject)):
        results = empty("По этим фильтрам задач нет.") + back("/tasks", "Сбросить фильтры")
    else:
        results = empty("Пока нет задач. Создай первую задачу, чтобы видеть её срок на главной.")
    body += panel("Мои задачи", results, link("/tasks/new", "Создать задачу"))
    return page(request, "Задачи", body)


def task_form(request, db, tid=None, *, error="", values=None):
    user = request.user
    row = workspace.task(db, user, tid) if tid else None
    if values is None:
        values = dict(row) if row else {}
        if row:
            values["due"] = date_time(row["due_at"], user["timezone"])
            values["subject"] = row["subject_id"]
            values["description"] = lecture_channel.task_description(db, user, row)
        elif request.query("subject") in {x["id"] for x in workspace.subjects(db, user)}:
            values["subject"] = request.query("subject")
    fields = input_field("title", "Название", values.get("title", ""), 'maxlength="120" required')
    fields += textarea("description", "Описание", 1600, value=values.get("description", ""))
    fields += select_field("subject", "Дисциплина", subject_choices(db, user), values.get("subject") or "")
    fields += input_field("due", "Срок (необязательно)", values.get("due", ""), kind="datetime-local")
    fields += select_field("priority", "Приоритет", workspace.PRIORITIES.items(), values.get("priority", "normal"))
    if row:
        fields += hidden("version", row["version"])
    route = f"/tasks/{tid}/edit" if tid else "/tasks/new"
    message = f'<p class="error" role="alert">{e(error)}</p>' if error else ""
    body = panel("Данные задачи", message + form(request, route, fields + button("Сохранить задачу"), command=True))
    return page(request, "Изменить задачу" if tid else "Новая задача", body + back(f"/tasks/{tid}" if tid else "/tasks", "Назад"))


def notes_panel(request, db, kind, target):
    rows = workspace.notes(db, request.user, kind, target)
    items = "".join(f'<li><div><p class="multiline">{e(row["body"])}</p>{link(f"/notes/{row['id']}", "Изменить заметку")}</div></li>'
                    for row in rows)
    stories = f'<ul class="rows">{items}</ul>' if items else empty("Заметок пока нет.")
    fields = textarea("body", "Новая заметка", 1600, required=True) + button("Добавить заметку")
    return panel("Личные заметки", stories + form(request, f"/notes/{kind}/{target}", fields, command=True))


def task_detail(request, db, tid, *, error=""):
    from .preparation import effort

    user = request.user
    row = workspace.task(db, user, tid)
    due = local_label(row["due_at"], user["timezone"]) if row["due_at"] else "Без срока"
    status = "Готово" if row["state"] == "done" else "В работе"
    details = f"{status} · Срок задачи: {due} · Приоритет: {workspace.PRIORITIES[row['priority']]}"
    body = f'<p>{e(details)}</p><p class="multiline">{e(lecture_channel.task_description(db, user, row))}</p>'
    assigned = next(iter(coursework.student_list(db, user, task_id=tid)), None)
    if assigned:
        body += f'<p><strong>{e(coursework.student_status(assigned))}</strong> · {e(coursework.student_next_step(assigned))}</p>'
        submission_due = local_label(assigned["due_at"], user["timezone"])
        body += f'<p class="meta">Срок сдачи учебной работы · {e(submission_due)}</p>'
        body += "<p>" + link("/work/" + assigned["id"], "К учебной работе", "dean-secondary-action") + "</p>"
    elif row["lecture_id"]:
        body += '<p>' + link("/lectures/" + row["lecture_id"], "К лекции и домашнему заданию") + "</p>"
    else:
        body += '<p class="meta">Это личная задача. Способ сдачи здесь не указан. Если нужно отправить результат, уточни способ у преподавателя.</p>'
    if row["exam_result"]:
        body += f'<p>{link(f"/grades/{row['exam_result']}", "Экзамен и план подготовки")}</p>'
    body += f'<p>{link(f"/tasks/{tid}/edit", "Редактировать")}</p>'
    subtasks = workspace.subtasks(db, user, tid)
    checked = sum(subtask["done"] for subtask in subtasks)
    state, action = ("Вернуть в работу", "reopen") if row["state"] == "done" else ("Завершить задачу", "complete")
    version = hidden("version", row["version"])
    buttons = ""
    if not assigned:
        confirm = ('<label class="check"><input name="complete_subtasks" value="1" type="checkbox" required>'
                   'Завершить оставшиеся подзадачи вместе с задачей</label>'
                   if action == "complete" and checked < len(subtasks) else "")
        buttons = form(request, f"/tasks/{tid}/{action}", version + confirm + button(state), command=True)
    buttons += form(request, f"/tasks/{tid}/delete", version + '<button class="quiet">Удалить задачу</button>', command=True)
    if error:
        body += f'<p class="error" role="alert">{e(error)}</p>'
    heading = panel("Сведения", body + '<div class="grid">' + buttons + "</div>")
    if subtasks:
        content = f'<p>Готово {checked} из {len(subtasks)}</p>'
        for subtask in subtasks:
            sid = subtask["id"]
            label = "Вернуть" if subtask["done"] else "Выполнить"
            action = "reopen" if subtask["done"] else "complete"
            content += f'<p>{e(subtask["title"])} · {"Готово" if subtask["done"] else "В работе"}</p>'
            content += form(request, f"/tasks/{tid}/subtasks/{sid}/{action}", button(label), command=True)
            content += form(request, f"/tasks/{tid}/subtasks/{sid}/delete", '<button class="quiet">Удалить подзадачу</button>', command=True)
    else:
        content = empty("Подзадач пока нет.")
    new = input_field("title", "Новая подзадача", options="required maxlength=120") + button("Добавить подзадачу")
    heading += panel("Подзадачи", content + form(request, f"/tasks/{tid}/subtasks", new, command=True))
    estimate = input_field("minutes", "Оценка длительности, минут", effort(db, tid) or "",
                           'min="1" max="1440"', "number")
    prepare = link(f"/study/preparation?task={tid}", "Рассчитать подготовку") + " · " + link(f"/study/timer?task={tid}", "Таймер")
    heading += panel("Моя подготовка", form(request, f"/tasks/{tid}/effort", estimate + button("Сохранить оценку"),
                                            command=True), prepare)
    heading += notes_panel(request, db, "task", tid)
    return page(request, row["title"], heading + back("/tasks", "К задачам"), row["subject"] or "")


def note_detail(request, db, nid):
    row, href = workspace.note(db, request.user, nid)
    fields = textarea("body", "Текст заметки", 1600, required=True, value=row["body"]) + button("Сохранить заметку")
    body = form(request, f"/notes/{nid}", fields, command=True)
    body += form(request, f"/notes/{nid}/delete", '<button class="quiet">Удалить заметку</button>', command=True)
    return page(request, "Личная заметка", panel("Заметка", body) + back(href, "К связанной записи"))


def event_form(request, db, eid=None, *, error="", values=None):
    user = request.user
    workspace.scope(user)
    row = workspace.event(db, user, eid) if eid else None
    if values is None:
        values = dict(row) if row else {}
        values["all_day"] = "1" if row and row["all_day"] else ""
        if row:
            values["starts"] = date_time(row["starts_at"], user["timezone"])
            values["ends"] = date_time(row["ends_at"], user["timezone"])
            values["first_day"] = values["starts"][:10]
            end_day = date.fromisoformat(values["ends"][:10])
            values["last_day"] = (end_day - timedelta(days=1)).isoformat() if row["all_day"] else end_day.isoformat()
    checked = " checked" if values.get("all_day") == "1" else ""
    fields = input_field("title", "Название", values.get("title", ""), 'maxlength="120" required')
    fields += input_field("location", "Место", values.get("location", ""), 'maxlength="120"')
    fields += textarea("description", "Описание", 1600, value=values.get("description", ""))
    fields += '<p class="meta">Для встречи укажи дату и время. Для события на весь день включи отметку и заполни даты.</p>'
    fields += input_field("starts", "Начало (дата и время)", values.get("starts", ""), kind="datetime-local")
    fields += input_field("ends", "Окончание (дата и время)", values.get("ends", ""), kind="datetime-local")
    fields += f'<label class="check"><input name="all_day" value="1" type="checkbox"{checked}> На весь день</label>'
    fields += input_field("first_day", "Первый день", values.get("first_day", ""), kind="date")
    fields += input_field("last_day", "Последний день включительно", values.get("last_day", ""), kind="date")
    fields += input_field("repeat_until", "Повторять еженедельно до даты", values.get("repeat_until") or "", kind="date")
    fields += '<p class="meta">Оставь дату повтора пустой для одного события. В серии до 26 событий.</p>'
    if row:
        fields += hidden("version", row["version"])
    path = f"/events/{eid}/edit" if eid else "/events/new"
    feedback = f'<p class="error" role="alert">{e(error)}</p>' if error else ""
    return page(request, "Изменить событие" if eid else "Новое личное событие",
                panel("Событие", feedback + form(request, path, fields + button("Сохранить событие"), command=True))
                + back("/schedule", "К календарю"))


def event_detail(request, db, eid):
    user = request.user
    row = workspace.event(db, user, eid)
    day = request.query("occurrence")
    instances = dict((d, (start, end)) for d, start, end in workspace.occurrences(row, user["timezone"]))
    first = next(iter(instances))
    if not day:
        day = first
    if day not in instances:
        raise DomainError(404, "Повторение недоступно.")
    start, end = instances[day]
    version = hidden("version", row["version"])
    if row["all_day"]:
        last = local_value(end, user["timezone"]).date() - timedelta(days=1)
        times = f'{local_value(start, user["timezone"]):%d.%m.%Y} — {last:%d.%m.%Y} · Весь день'
    else:
        times = f"{local_label(start, user['timezone'])} — {local_label(end, user['timezone'])}"
    body = f'<p>{e(times)}</p><p>{e(row["location"])}</p><p class="multiline">{e(row["description"])}</p>'
    if row["repeat_until"]:
        skipped = db.execute("SELECT 1 FROM personal_event_skip WHERE event_id=? AND occurrence_day=?", (eid, day)).fetchone()
        body += f'<p>Еженедельный повтор до {e(row["repeat_until"])}.</p>'
        if skipped:
            body += "<p>Это повторение отменено.</p>"
        else:
            body += form(request, f"/events/{eid}/skip", version + hidden("day", day) + button("Отменить это повторение"), command=True)
    body += f'<p>{link(f"/events/{eid}/edit", "Изменить событие")}</p>'
    body += form(request, f"/events/{eid}/delete", version + '<button class="quiet">Удалить событие</button>', command=True)
    return page(request, row["title"], panel("Личное событие", body) + back("/schedule", "К календарю"))


def subject_recordings(request, db, subject_id, period_id):
    from . import campus_lab, lecture_channel

    user = request.user
    if not campus_lab.enabled(user) or period_id != user["period_id"]:
        return ""
    records = lecture_channel.subject_lectures(db, user, subject_id, period_id)
    items = []
    for row in records:
        path = f'/lectures/{row["id"]}'
        resources = []
        if row["has_notes"]:
            resources.append(link(path + "#notes", "Конспект"))
        if row["has_homework"]:
            resources.append(link(path + "#homework", "Домашнее задание"))
        state = "Запись" if row["ended"] else "Прямой эфир"
        details = " · ".join((state, *resources))
        items.append(f'<li><div>{link(path, row["title"])}<p class="meta">{details}</p></div></li>')
    content = ('<p class="meta">Это записи по дисциплине. Они могут относиться к другим парам.</p>'
               f'<ul class="rows">{"".join(items)}</ul>' if items else
               '<p class="meta">Для этой группы по дисциплине пока нет записей или материалов.</p>')
    return panel("Материалы — записи группы", content, link("/lectures", "Все записи группы"))


def lesson_assignments(request, db, lesson):
    user = request.user
    rows = (coursework.student_list(db, user, subject_id=lesson.subject_id, period_id=lesson.period_id)
            if lesson.subject_id else [])
    content = (coursework_views.student_work_rows(rows, user["timezone"]) if rows else
               '<p class="meta">Преподаватель пока не опубликовал здесь работу для сдачи.</p>')
    return panel("Учебные работы по дисциплине", content, link("/work", "Мои учебные работы"))


def components_panel(rows, zone, assignments=()):
    if not rows:
        return panel("Компоненты оценки", empty("В ведомости по этой дисциплине пока нет компонентов оценки."))
    by_assessment = {work["assessment_id"]: work for work in assignments}
    items = []
    for row in rows:
        points = "—" if row["points"] is None else row["points"]
        bound = row["max_points"] if row["max_points"] is not None else "—"
        due = local_label(row["due_at"], zone) if row["due_at"] else "Дата не задана"
        details = f'Срок: {due} · Вес: {learning.format_weight(row["weight_bp"])}'
        work = by_assessment.get(row["id"])
        channel = ""
        if work is not None:
            channel = f'<p>{link("/work/" + work["id"], "Открыть учебную работу")}</p>'
        elif row["kind"] == "coursework":
            channel = '<p class="meta">Для этой записи в ведомости нет отдельной карточки сдачи.</p>'
        items.append(f'<li><div><strong>{e(row["title"])}</strong><p class="meta">{e(details)}</p>{channel}</div>'
                     f'<div>{e(points)} / {e(bound)} · {e(learning.STATES[row["state"]])}</div></li>')
    return panel("Компоненты оценки", f'<ul class="rows results">{"".join(items)}</ul>')


def grade_scenario(request, db, row, components):
    saved = learning.scenario(db, request.user, row["id"])
    target = saved["target"] if saved else None
    expected = saved["exam_points"] if saved else None
    result = learning.scores(components, target, expected)
    if "problem" in result:
        return panel("Расчёт оценки", f'<p>{e(result["problem"])}</p>')
    hints = ("Сумма (баллы ÷ максимум × вес в процентах) по всем работам. "
             "Итог округляется один раз до ближайшего целого, половина вверх.")
    fields = input_field("target", "Целевой итог из 100", "" if target is None else target, 'min="0" max="100" step="1"', "number")
    fields += input_field("exam_points", "Предполагаемые баллы за экзамен", "" if expected is None else expected,
                          f'min="0" max="{result["max_exam"]}" step="1"', "number")
    details = f'<p>{hints}</p><p>Максимум экзамена: {result["max_exam"]}. Вес: {learning.format_weight(result["exam_weight"])}.</p>'
    if "forecast" in result:
        details += f'<p>Прогноз при указанных баллах: <strong>{result["forecast"]} / 100</strong>.</p>'
    if target is not None:
        required = result.get("required")
        details += (f'<p>Для цели {target} нужно минимум <strong>{required} / {result["max_exam"]}</strong> за экзамен.</p>'
                    if required is not None else f'<p>Цель {target} недостижима. Максимальный итог: {result["best"]} / 100.</p>')
    details += form(request, f'/grades/{row["id"]}/scenario', fields + button("Рассчитать и сохранить"), command=True)
    return panel("Личный расчёт оценки", details)


def preparation(request, db, row):
    user = request.user
    plan = workspace.plan_for(db, user, row["id"])
    if plan is None:
        if not row["exam_at"] or datetime.fromisoformat(row["exam_at"]) <= now():
            return ""
        return panel("Подготовка", form(request, f'/grades/{row["id"]}/plan',
                                         button("Создать план из шаблона"), command=True))
    body = '<p>План из трёх шагов. Сроки и задачи можно редактировать.</p>'
    if plan["exam_at"] != plan["current_exam_at"]:
        body += '<p class="change">Дата экзамена изменилась. Пересмотри сроки задач.</p>'
    rows = [item for item in workspace.tasks(db, user, subject=row["subject_id"]) if item["plan_id"] == plan["id"]]
    return panel("План подготовки", body + task_rows(rows, user["timezone"]))


def grade_revisions(db, rid, zone):
    rows = learning.revisions(db, rid)
    items = []
    for row in rows:
        old, new = json.loads(row["old_json"]), json.loads(row["new_json"])
        actor = f'{row["actor"]} · {local_label(row["created_at"], zone)}'
        fields = {"score": "Итог", "points": "Баллы", "state": "Статус", "outcome": "Результат", "exam_at": "Дата экзамена"}
        def display(key, value):
            if value is None:
                return "—"
            if key == "state":
                return learning.STATES[value]
            if key == "outcome":
                return {"passed": "Сдано", "failed": "Не сдано"}[value]
            return local_label(value, zone) if key == "exam_at" else value
        changes = "".join(f'<p>{label}: {e(display(key, old[key]))} → {e(display(key, new[key]))}</p>'
                          for key, label in fields.items() if key in old and old[key] != new[key])
        items.append(f'<li><strong>{e(new["record"])}</strong><p class="meta">{e(actor)}</p>{changes}</li>')
    return panel("История оценок", f'<ol class="timeline">{"".join(items)}</ol>') if items else ""


def staff_grades(request, db):
    rows = learning.staff_results(db, request.user)
    items = "".join(f'<li><div>{link(f"/staff/grades/{row['id']}", row["subject"])}'
                    f'<p class="meta">{e(row["student_name"])} · {e(row["group_name"])}</p></div></li>' for row in rows)
    return page(request, "Учебные результаты", panel("Доступные группы", f'<ul class="rows">{items}</ul>' if items else
                                                     empty("Доступных ведомостей нет.")) + back("/staff", "В кабинет сотрудника"))


def staff_grade(request, db, rid):
    user = request.user
    row = learning.staff_results(db, user, rid)
    version = hidden("version", row["version"])
    fields = version
    if row["kind"] == "credit":
        fields += select_field("outcome", "Итог зачёта", [("", "Не выставлен"), ("passed", "Зачтено"),
                                                         ("failed", "Не зачтено")], row["outcome"] or "")
    else:
        fields += input_field("score", "Итоговый балл", row["score"] if row["score"] is not None else "",
                              'min="0" max="100" step="1"', "number")
        fields += input_field("exam_at", "Дата экзамена", date_time(row["exam_at"], user["timezone"]), kind="datetime-local")
    body = panel("Ведомость", form(request, f"/staff/grades/{rid}", fields + button("Сохранить ведомость"), command=True))
    components = learning.assessments(db, rid)
    debt = db.execute("SELECT 1 FROM retake_debt WHERE id=?", (rid,)).fetchone()
    if debt:
        body += panel("Пересдача", link("/staff/retakes/" + rid, "К задолженности"))
    else:
        missed = [c for c in components if c["kind"] == "coursework" and c["due_at"]
                  and c["due_at"] < timestamp() and c["state"] in {"ungraded", "failed"}]
        if row["outcome"] == "failed" or missed:
            choices = [("", "Не сдан итог"), *((c["id"], c["title"]) for c in missed)]
            inputs = select_field("assessment", "Основание", choices)
            inputs += input_field("reason", "Причина", options='maxlength="300" required')
            inputs += textarea("conditions", "Условия допуска", 500, required=True)
            body += panel("Задолженность", form(request, f"/staff/retakes/{rid}/open",
                          inputs + button("Зафиксировать задолженность"), command=True))
    for component in components:
        points = "" if component["points"] is None else component["points"]
        fields = version + hidden("component", component["id"])
        fields += select_field("state", "Статус работы", learning.STATES.items(), component["state"])
        fields += input_field("points", "Баллы за работу", points, kind="number")
        body += panel(component["title"], form(request, f"/staff/grades/{rid}/component", fields + button("Сохранить работу"), command=True))
    body += grade_revisions(db, rid, user["timezone"]) + back("/staff/grades", "К ведомостям")
    return page(request, row["subject"], body, f'{row["student_name"]} · {row["group_name"]}')
