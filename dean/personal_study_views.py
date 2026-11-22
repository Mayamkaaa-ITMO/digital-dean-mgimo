from . import personal_study as study, public_schedule, study_views, workspace
from .ui import back, button, e, empty, form, hidden, input_field, link, local_label, page, panel, select_field, textarea


def origin_label(row):
    if row["source_id"] is None:
        return "Добавлен вручную"
    source = public_schedule.SOURCES.get(row["source_id"], {})
    group = source.get("groups", {}).get(row["group_id"], {})
    return " · ".join((source.get("name", row["source_id"]), group.get("label", row["group_id"]), row["period"]))


def index(request, db):
    rows = study.subjects(db, request.user)
    items = "".join(f'<li><div>{link(f"/study/subjects/{row["id"]}", row["title"])}'
                    f'<p class="meta">{e(origin_label(row))}</p></div></li>' for row in rows)
    links = [link("/grades", "Успеваемость"), link("/work", "Учебные работы"), link("/curriculum", "Учебный план"),
             link("/retakes", "Пересдачи"), link("/study/preparation", "Подготовка"), link("/study/timer", "Таймер")]
    if request.public:
        links.insert(0, link("/study/public/subjects", "Дисциплины расписания"))
    body = '<nav class="rw-only rw-study-links switcher" aria-label="Разделы учёбы">' + " ".join(links) + '</nav>'
    body += panel("Сохранённые предметы", f'<ul class="rows">{items}</ul>' if items else empty("Предметов пока нет."))
    fields = input_field("title", "Название", options='maxlength="160" required')
    body += panel("Добавить вручную", form(request, "/study/subjects", fields + button("Добавить"), command=True))
    options = [(name, name) for name in study.suggestions(db, request.public)]
    if options:
        fields = select_field("source_title", "Предмет из выбранной группы", options)
        body += panel("Из расписания", form(request, "/study/subjects", fields + button("Сохранить предмет"), command=True))
    return page(request, "Мои предметы", body)


def action_form(request, row, action, fields, caption):
    return form(request, f'/study/subjects/{row["id"]}/{action}',
                hidden("revision", row["revision"]) + fields + button(caption), command=True)


def result_panel(request, db, row):
    stored, calculated = study.result(db, row["id"])

    def value(name, default=""):
        return stored[name] if stored and stored[name] is not None else default

    fields = input_field("coursework", "Работы из 100", value("coursework"), 'min="0" max="100"', "number")
    fields += input_field("exam_points", "Экзамен из 100", value("exam_points"), 'min="0" max="100"', "number")
    fields += input_field("weight", "Вес экзамена, %", value("exam_weight", 50), 'min="1" max="100" required', "number")
    fields += input_field("target", "Цель из 100", value("target"), 'min="0" max="100"', "number")
    text = '<p class="meta">Введено мной · для планирования</p>'
    if calculated:
        if calculated.get("problem"):
            text += f'<p>{e(calculated["problem"])}</p>'
        else:
            text += f'<p>Максимум по текущим правилам: {e(calculated["best"])} из 100.</p>'
            if "forecast" in calculated:
                text += f'<p>Мой расчёт: {e(calculated["forecast"])} из 100.</p>'
            if "required" in calculated:
                points = calculated["required"]
                text += f'<p>{e("Для цели нужно " + str(points) + " из 100 за экзамен." if points is not None else "При этих баллах цель недостижима.")}</p>'
    return panel("Личные результаты", text + action_form(request, row, "result", fields, "Сохранить расчёт"))


def detail(request, db, sid):
    user = request.user
    row = study.subject(db, user, sid)
    fields = input_field("title", "Название", row["title"], 'maxlength="160" required')
    fields += textarea("note", "Моя заметка", 1600, value=row["note"])
    body = back("/study/subjects", "Мои предметы")
    body += '<p>' + link(f"/study/subjects/{sid}/materials", "Материалы предмета") + " · " + link(
        "/study/preparation", "План подготовки") + "</p>"
    body += panel("Предмет и заметка", action_form(request, row, "save", fields, "Сохранить"))
    selection = (row["source_id"], row["group_id"])
    if row["source_id"]:
        entries = study.related_lessons(db, row)
        zone = public_schedule.SOURCES.get(row["source_id"], {}).get("zone", user["timezone"])
        items = ""
        for entry in entries:
            label = f'{entry["kind"]} · {local_label(entry["starts_at"], zone)}'
            item = link("/schedule/public/" + entry["id"], label) if request.public == selection else e(label)
            items += f"<li>{item}</li>"
        content = f'<p>{e(row["source_title"])}</p>'
        if items:
            content += f'<ul class="rows">{items}</ul>'
            if request.public != selection:
                content += link("/schedule/context", "Открыть связанную группу")
        else:
            content += empty("Ближайших занятий в опубликованных данных нет.")
        if row["source_id"] in public_schedule.SOURCES:
            source = public_schedule.SOURCES[row["source_id"]]
            content += "".join(f'<p>{link(item["url"], item["label"])}</p>'
                               for item in source["services"] if item.get("id") == "programs")
        content += action_form(request, row, "unlink", "", "Убрать связь с расписанием")
        body += panel("Связанное расписание", content)
    elif request.public:
        options = [(name, name) for name in study.suggestions(db, request.public)]
        if options:
            field = select_field("source_title", "Предмет и группа", options)
            body += panel("Связать с расписанием", action_form(request, row, "link", field, "Подтвердить связь"))
    tasks = study.linked_tasks(db, user, sid)
    task_list = "".join(f'<li><div>{link("/tasks/" + task["id"], task["title"])}'
                        + action_form(request, row, "detach", hidden("task_id", task["id"]), "Убрать связь")
                        + "</div></li>" for task in tasks)
    existing = {task["id"] for task in tasks}
    options = [(task["id"], task["title"]) for task in workspace.tasks(db, user) if task["id"] not in existing]
    content = f'<ul class="rows">{task_list}</ul>' if task_list else empty("Связанных задач пока нет.")
    if options:
        content += action_form(request, row, "task", select_field("task_id", "Моя задача", options), "Привязать задачу")
    body += panel("Свои задачи", content, link("/tasks/new", "Создать задачу"))
    body += result_panel(request, db, row)
    return page(request, row["title"], body, origin_label(row))
