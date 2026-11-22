from . import achievements, courses, portfolio, practice
from .domain import timestamp
from .schedule import today_for
from .ui import back, button, e, empty, form, hidden, input_field, link, local_label, page, panel, select_field, textarea


def practice_list(request, db, manage=False):
    cards = []
    prefix = "/staff/practice" if manage else "/practice"
    for row in practice.cases(db, request.user, manage=manage):
        title = row["title"] + (" · " + row["student"] if manage else "")
        cards.append(f'<li><div>{link(prefix + "/" + row["id"], title)}'
                     f'<p>{e(practice.STATES[row["state"]])}</p></div></li>')
    body = (f'<ul class="rows">{"".join(cards)}</ul>' if cards else
            empty("Назначенных практик пока нет. После назначения здесь появятся сроки и дневник."))
    return page(request, "Назначенные практики" if manage else "Мои практики", panel("Практики", body)
                + back("/staff" if manage else "/opportunities", "Назад"))


def practice_case(request, db, pid, manage=False):
    user = request.user
    row = practice.cases(db, user, pid, manage=manage)
    zone = user["timezone"]
    path = f"/staff/practice/{pid}" if manage else f"/practice/{pid}"
    state = row["state"]
    header = f'<p>{e(practice.STATES[state])}</p><p>Руководитель: {e(row["supervisor"])}</p>'
    header += f'<p>Начало {e(local_label(row["starts_at"], zone))} · Отчёт до {e(local_label(row["due_at"], zone))}.</p>'
    if state in {"active", "changes"} and row["due_at"] < timestamp():
        header += '<p class="change">Срок сдачи прошёл.</p>'
    header += f'<p>{link("/requests/" + row["application_id"], "Решение по заявке")}</p>'
    body = panel("Сведения", header)
    entries = "".join(f'<li><div><strong>{e(item["day"])}</strong><p class="multiline">{e(item["body"])}</p></div></li>'
                      for item in db.execute("SELECT * FROM practice_diary WHERE case_id=? ORDER BY day", (pid,)))
    diary = f'<ul class="rows">{entries}</ul>' if entries else empty("Записей пока нет.")
    if not manage and state in {"active", "changes"}:
        fields = input_field("day", "Дата работы", today_for(user), "required", "date")
        fields += textarea("body", "Что сделано", 1800, required=True) + button("Добавить запись")
        diary += form(request, path + "/diary", fields, command=True)
    body += panel("Дневник", diary)
    reports = "".join(f'<li><strong>Версия {item["revision"]} · {e(local_label(item["submitted_at"], zone))}</strong>'
                      f'<p class="multiline">{e(item["body"])}</p></li>'
                      for item in db.execute("SELECT * FROM practice_report WHERE case_id=? ORDER BY revision DESC", (pid,)))
    report = f'<ol class="timeline">{reports}</ol>' if reports else empty("Отчёт пока не отправлен.")
    version = hidden("version", row["version"])
    if not manage and state in {"active", "changes"}:
        fields = version + textarea("body", "Текст новой версии", 2500, required=True) + button("Отправить отчёт")
        report += form(request, path + "/report", fields, command=True)
    elif manage and state == "review":
        options = [(key, label) for key, label in practice.STATES.items() if key in {"changes", "passed", "failed"}]
        fields = version + select_field("state", "Решение", options)
        fields += textarea("feedback", "Комментарий руководителя", 600, required=True)
        report += form(request, path + "/review", fields + button("Сохранить решение"), command=True)
    body += panel("Отчёт", report)
    history = "".join(f'<li><strong>{e(practice.STATES[item["state"]])}</strong>'
                      f'<p class="meta">{e(local_label(item["created_at"], zone))}</p><p>{e(item["note"])}</p></li>'
                      for item in db.execute("SELECT * FROM practice_history WHERE case_id=? ORDER BY id", (pid,)))
    body += panel("История", f'<ol class="timeline">{history}</ol>')
    return page(request, row["title"], body + back("/staff/practice" if manage else "/practice", "К практикам"),
                row["student"] if manage else "Демонстрационная практика")


def review_list(request, db):
    rows = achievements.requests(db, request.user)
    parts = []
    labels = {"pending": "На проверке", "accepted": "Подтверждено", "refused": "Отказ"}
    for row in rows:
        body = f'<p>{e(row["full_name"])} · {e(labels[row["state"]])}</p><p class="multiline">{e(row["content"])}</p>'
        if row["state"] == "pending":
            fields = hidden("request", row["requested_at"]) + select_field("state", "Решение",
                       [("accepted", "Подтвердить"), ("refused", "Отказать")])
            fields += textarea("note", "Основание", 400, required=True) + button("Сохранить")
            body += form(request, f'/staff/achievements/{row["entry_id"]}', fields, command=True)
        elif row["feedback"]:
            body += f'<p>{e(row["feedback"])}</p>'
        parts.append(panel(row["title"], body))
    full = "".join(parts) or empty("Запросов для доступных групп нет.")
    return page(request, "Подтверждение достижений", full + back("/staff", "В кабинет"))


def achievement_actions(request, db, row):
    if row["kind"] != "achievement":
        return ""
    if row["verified_at"]:
        label = local_label(row["verified_at"], request.user["timezone"])
        return f'<p><span class="tag">Подтверждено сотрудником</span> · {e(label)}</p>'
    existing = achievements.review_state(db, row["id"])
    pending = existing and existing["state"] == "pending"
    action, caption = ("withdraw", "Отозвать запрос") if pending else ("verify", "Запросить подтверждение")
    context = '<p><span class="tag">Добавлено студентом</span></p>'
    if pending:
        context += "<p>Запрос на проверке.</p>"
    elif existing and existing["feedback"]:
        context += f'<p>Комментарий сотрудника: {e(existing["feedback"])}</p>'
    return context + form(request, f'/portfolio/{row["id"]}/{action}', button(caption), command=True)


def resume_page(request, db):
    user = request.user
    options = portfolio.entries(db, user)[:18]
    fields = input_field("title", "Заголовок", "Учебное резюме", 'required maxlength="120"')
    fields += '<label class="check"><input type="checkbox" name="name" value="1">Включить имя из демо-профиля</label>'
    for row in options:
        fields += f'<label class="check"><input type="checkbox" name="item_{row["id"]}" value="1">{e(row["title"])}</label>'
    body = (form(request, "/portfolio/resume", fields + button("Собрать PDF"), command=True)
            if options else empty("Сначала добавь хотя бы одну запись портфолио."))
    listing = "".join(f'<li><div>{link("/portfolio/resume/" + row["id"], row["title"])}'
                      f'<p>{e(local_label(row["created_at"], user["timezone"]))}</p></div></li>'
                      for row in achievements.exports(db, user))
    return page(request, "Резюме", panel("Выбери записи", body)
                + panel("Сохранённые резюме", f'<ul class="rows">{listing}</ul>' if listing else empty("Файлов пока нет."))
                + back("/portfolio", "К портфолио"), "PDF доступен только из твоего кабинета.")


def resume_detail(request, db, rid):
    row = achievements.exports(db, request.user, rid)
    return page(request, row["title"], panel("Личный PDF", link(f"/portfolio/resume/{rid}/download", "Скачать PDF"))
                + back("/portfolio/resume", "К резюме"))


def course_list(request, db):
    user = request.user
    q, category = request.query("q"), request.query("category")
    favorites = request.query("favorites") == "1"
    listable = courses.listing(db, user, q=q, category=category, favorites=favorites)
    categories = sorted({row["category"] for row in courses.listing(db, user)})
    fields = input_field("q", "Поиск", q, 'maxlength="100"', "search")
    fields += select_field("category", "Категория", [("", "Все"), *((x, x) for x in categories)], category)
    fields += f'<label class="check"><input type="checkbox" name="favorites" value="1"{" checked" if favorites else ""}>Избранное</label>'
    selector = f'<form method="get" action="/courses" class="inline">{fields}{button("Показать")}</form>'
    items = "".join(f'<li><div>{link("/courses/" + row["id"], row["title"])}'
                    f'<p>{e(row["category"])} · {e(row["description"])}</p></div></li>' for row in listable)
    return page(request, "Демо-курсы", selector + panel("Каталог", f'<ul class="rows">{items}</ul>' if items else
                empty("Подходящих курсов нет.")) + back("/student-services", "К сервисам"))


def course_detail(request, db, cid):
    row = courses.listing(db, request.user, cid)
    path = "/courses/" + cid
    info = f'<p>{e(row["description"])} · Автор: {e(row["author"])}</p>'
    info += f'<p>Запись до {e(local_label(row["closes_at"], request.user["timezone"]))}.</p>'
    favorite, label = ("unfavorite", "Убрать из избранного") if row["favorite"] else ("favorite", "В избранное")
    info += form(request, path + "/" + favorite, button(label), command=True)
    if not row["enrolled_at"] and row["closes_at"] > timestamp():
        info += form(request, path + "/enroll", button("Записаться"), command=True)
    body = panel("О курсе", info)
    if row["enrolled_at"]:
        items = "".join(f'<li><div>{link(f"{path}/lessons/{lesson["id"]}", lesson["title"])}'
                        f'<p>{"Готово" if lesson["finished_at"] else "Предстоит"}</p></div></li>'
                        for lesson in courses.lessons(db, request.user, cid))
        body += panel(f'Уроки · {row["finished"]} из {row["lessons"]}', f'<ul class="rows">{items}</ul>')
    return page(request, row["title"], body + back("/courses", "К каталогу"))


def course_lesson(request, db, cid, lid):
    row = courses.lesson(db, request.user, cid, lid)
    info = f'<p class="multiline">{e(row["content"])}</p>'
    if not row["finished_at"]:
        info += form(request, f"/courses/{cid}/lessons/{lid}/finish", button("Отметить пройденным"), command=True)
    else:
        info += "<p>Урок пройден.</p>"
    return page(request, row["title"], panel("Материал", info) + back(f"/courses/{cid}", "К курсу"))
