from itertools import groupby

from . import academic_views, career_views, curriculum, lending, library_queue, portfolio, practice, workspace
from .domain import timestamp
from .ui import (back, button, e, empty, form, hidden, input_field, link, local_label,
                 page, panel, select_field, textarea)


SERVICE_LINKS = (("/activities", "Афиша"), ("/curriculum", "Учебный план"), ("/library", "Библиотека"),
               ("/portfolio", "Портфолио"), ("/opportunities", "Практики и проекты"),
               ("/finance", "Финансы"), ("/residence", "Проживание"), ("/equipment", "Техника кампуса"),
               ("/teams", "Совместная учёба"), ("/clubs", "Клубы"), ("/courses", "Демо-курсы"))


def services_hub(request, db):
    workspace.scope(request.user)
    return page(request, "Сервисы", panel("Студенческие сервисы", '<ul class="rows">' +
                "".join(f"<li>{link(path, label)}</li>" for path, label in SERVICE_LINKS) + "</ul>"))


def curriculum_page(request, db):
    user = request.user
    plan = curriculum.items(db, user)
    body = ""
    current = timestamp()
    for _, values in groupby(plan, key=lambda row: row["period_id"]):
        items = list(values)
        required = [row for row in items if row["kind"] == "required"]
        electives = [row for row in items if row["kind"] == "elective"]
        credits = sum(row["credits"] for row in required)
        earned = sum(row["credits"] for row in required if row["outcome"] == "passed")
        content = f'<p>Обязательные дисциплины. Зачтено кредитов {earned} из {credits}.</p>'
        if required:
            rows = ""
            for row in required:
                title = link(f'/grades/{row["result_id"]}', row["title"]) if row["result_id"] else e(row["title"])
                state = {"passed": "Зачтено", "failed": "Нужна пересдача", None: "Результат отсутствует"}[row["outcome"]]
                course_link = link(f'/curriculum/course/{row["id"]}', "О дисциплине")
                rows += f'<li><div>{title}<p class="meta">{row["credits"]} кр. · {e(state)} · {course_link}</p></div></li>'
            content += f'<ul class="rows">{rows}</ul>'
        if electives:
            content += "<h3>Выборный блок</h3><p>Можно выбрать один факультатив на семестр.</p>"
            for row in electives:
                end = local_label(row["window_end"], user["timezone"])
                left = row["capacity"] - row["taken"]
                details = f'{row["credits"]} кр. · Мест: {left} · Выбор до {end}'
                content += f'<p>{link(f"/curriculum/course/{row["id"]}", row["title"])}</p><p class="meta">{e(details)}</p>'
                if row["chosen"]:
                    content += '<p><span class="tag">Выбран</span></p>'
                elif row["period_id"] == user["period_id"] and row["window_start"] <= current < row["window_end"] and left > 0:
                    content += form(request, f'/curriculum/{row["id"]}/choose', button("Выбрать"), command=True)
                else:
                    content += '<p class="meta">Выбор сейчас недоступен.</p>'
        body += panel(items[0]["period"], content)
    body += academic_views.curriculum_panels(request, db)
    return page(request, "Учебный план", body or panel("Дисциплины", empty("Для программы пока нет набора дисциплин.")))


def library_page(request, db):
    user = request.user
    workspace.scope(user)
    library_queue.refresh(db, user)
    term = request.query("q")[:100]
    filters = input_field("q", "Поиск книги", term, 'maxlength="100"', "search") + button("Найти")
    selector = f'<form method="get" action="/library" class="inline">{filters}</form>'
    loans = {row["book_id"]: row for row in lending.loans(db, user) if not row["returned_at"]}
    holds = {row["book_id"]: row for row in library_queue.holds(db, user) if row["state"] in {"waiting", "offered"}}
    items = ""
    for row in lending.books(db, user, term):
        bid = row["id"]
        free = row["copies"] - row["lent"] - row["reserved"]
        items += f'<li><div><strong>{e(row["title"])}</strong><p class="meta">{e(row["author"])} · {e(row["topic"])}</p>'
        if loan := loans.get(bid):
            items += f'<p>{link("/library/" + loan["id"], "У тебя на руках · открыть выдачу")}</p>'
        elif hold := holds.get(bid):
            label = library_queue.STATES[hold["state"]] + " · открыть резерв"
            items += f'<p>{link("/library/holds/" + hold["id"], label)}</p>'
        elif not row["copies"]:
            items += '<p>Экземпляров в выдаче нет.</p>'
        else:
            if free:
                items += f'<p>Доступно экземпляров: {free} из {row["copies"]}.</p>'
            else:
                items += '<p>Свободных экземпляров сейчас нет. Можно встать в очередь.</p>'
            items += form(request, f'/library/books/{bid}/reserve',
                          button("Зарезервировать" if free else "Встать в очередь"), command=True)
        items += "</div></li>"
    listing = (f'<ul class="rows">{items}</ul>' if items else
               empty("Подходящих книг нет. Попробуй поискать по названию или автору."))
    return page(request, "Библиотека", selector + panel("Демонстрационный каталог", listing,
                link("/library/mine", "Мои книги")),
                "Резерв на два дня. Экземпляры переходят по порядку очереди. Выдачу подтверждает сотрудник.")


def loan_items(rows, zone, manage=False):
    texts = []
    for row in rows:
        state = "Возвращена" if row["returned_at"] else ("Срок прошёл" if row["due_at"] <= timestamp() else "На руках")
        href = f'/{ "staff/" if manage else ""}library/{row["id"]}'
        text = f'{state} · Срок: {local_label(row["due_at"], zone)}'
        if manage:
            text += f' · {row["student_name"]}'
        texts.append(f'<li><div>{link(href, row["title"])}<p class="meta">{e(text)}</p></div></li>')
    return f'<ul class="rows">{"".join(texts)}</ul>' if texts else empty("Выдач пока нет.")


def my_loans(request, db):
    user = request.user
    workspace.scope(user)
    library_queue.refresh(db, user)
    holds = "".join(f'<li><div>{link("/library/holds/" + row["id"], row["title"])}'
                    f'<p>{e(library_queue.STATES[row["state"]])}</p></div></li>' for row in library_queue.holds(db, user))
    return page(request, "Мои книги", panel("Резервы", f'<ul class="rows">{holds}</ul>' if holds else empty("Резервов нет."))
                + panel("Выдачи", loan_items(lending.loans(db, user), user["timezone"]))
                + back("/library", "К каталогу"))


def loan_detail(request, db, lid, manage=False):
    user = request.user
    row = lending.loans(db, user, lid, manage=manage)
    state = "Возвращена" if row["returned_at"] else "На руках"
    info = f'<p>{e(row["author"])} · {e(row["label"])}</p>'
    info += f'<p>{state}. Срок возврата: {e(local_label(row["due_at"], user["timezone"]))}.</p>'
    if manage:
        info += f'<p>Читатель: {e(row["student_name"])}</p>'
        if not row["returned_at"]:
            info += form(request, f"/staff/library/{lid}/return", button("Принять возврат"), command=True)
    elif not row["returned_at"] and not row["renewal_count"] and timestamp() < row["due_at"]:
        info += form(request, f"/library/{lid}/renew", button("Продлить на 7 дней"), command=True)
    return page(request, row["title"], panel("Сведения о выдаче", info)
                + back("/staff/library" if manage else "/library/mine", "К выдачам"))


def staff_library(request, db):
    user = request.user
    students = lending.staff_students(db, user)
    library_queue.refresh(db, user)
    copies = lending.available_copies(db, user)
    if students and copies:
        fields = select_field("student", "Читатель", [(row["id"], f'{row["full_name"]} · {row["group_name"]}') for row in students])
        fields += select_field("copy", "Экземпляр", [(row["id"], f'{row["title"]} · {row["label"]}'
                               + (" · по резерву" if row["reserved"] else "")) for row in copies])
        issue = form(request, "/staff/library/issue", fields + button("Выдать на 14 дней"), command=True)
    else:
        issue = empty("Для выдачи нужны доступный экземпляр и читатель из разрешённой группы.")
    offers = []
    for row in library_queue.staff_offers(db, user):
        text = f'{row["title"]} · {row["label"]} · {row["full_name"]}'
        fields = hidden("student", row["student_id"]) + hidden("copy", row["copy_id"])
        offers.append(f'<li><div><p>{e(text)}</p>'
                      + form(request, f'/staff/library/issue/{row["id"]}', fields + button("Выдать по резерву"), command=True)
                      + "</div></li>")
    body = panel("Выдача", issue) + panel("Готовые резервы", f'<ul class="rows">{"".join(offers)}</ul>' if offers else
                       empty("Готовых резервов в доступных группах нет."))
    body += panel("Выдачи групп", loan_items(lending.loans(db,user,manage=True), user["timezone"], True))
    return page(request, "Библиотечная выдача", body + back("/staff", "К кабинету"))


def portfolio_page(request, db):
    items = ""
    for row in portfolio.entries(db, request.user):
        label = portfolio.KINDS[row["kind"]]
        if row["kind"] == "achievement":
            label = "Подтверждено сотрудником" if row["verified_at"] else "Добавлено студентом"
        items += f'<li><div>{link(f"/portfolio/{row["id"]}", row["title"])}<p class="meta">{e(label)}</p></div></li>'
    listing = f'<ul class="rows">{items}</ul>' if items else empty("Проектов и достижений пока нет.")
    actions = link("/portfolio/new", "Добавить запись") + link("/portfolio/resume", "Собрать резюме")
    return page(request, "Портфолио", panel("Личные записи", listing, actions))


def portfolio_form(request, db, eid=None):
    user = request.user
    workspace.scope(user)
    row = portfolio.entry(db,user,eid) if eid else {}
    fields = select_field("kind", "Тип", portfolio.KINDS.items(), row["kind"] if row else "project")
    fields += input_field("title", "Название", row["title"] if row else "", 'required maxlength="140"')
    fields += textarea("content", "Описание", 1600, value=row["content"] if row else "")
    fields += input_field("url", "Внешняя HTTPS-ссылка (необязательно)", row["url"] if row else "",
                          'maxlength="320"', "url")
    if row:
        fields += hidden("version", row["version"])
    route = f"/portfolio/{eid}/edit" if eid else "/portfolio/new"
    body = form(request, route, fields + button("Сохранить"), command=True)
    return page(request, "Изменить запись" if eid else "Новая запись портфолио",
                panel("Карточка", body) + back(f"/portfolio/{eid}" if eid else "/portfolio","Назад"))


def portfolio_detail(request, db, eid):
    row = portfolio.entry(db,request.user,eid)
    info = f'<p>{e(portfolio.KINDS[row["kind"]])}</p><p class="multiline">{e(row["content"])}</p>'
    if row["url"]:
        info += f'<p>{link(row["url"], "Открыть внешний материал")}</p>'
    info += career_views.achievement_actions(request, db, row)
    if not row["verified_at"]:
        info += f'<p>{link(f"/portfolio/{eid}/edit","Редактировать")}</p>'
    info += form(request, f"/portfolio/{eid}/delete", hidden("version", row["version"]) + button("Удалить запись"), command=True)
    return page(request, row["title"], panel("Личная запись", info) + back("/portfolio", "К портфолио"))


def opportunity_page(request, db):
    rows = portfolio.opportunities(db, request.user)
    items = ""
    for row in rows:
        status = portfolio.STATES.get(row["status"], "Добавить в личный трекер")
        items += f'<li><div>{link(f"/opportunities/{row["id"]}", row["title"])}'
        items += f'<p class="meta">{e(row["sponsor"])} · {e(status)}</p></div></li>'
    listing = f'<ul class="rows">{items}</ul>' if items else empty("Предложений пока нет.")
    return page(request, "Практики и проекты", panel("Демонстрационные предложения", listing, link("/practice", "Мои практики")))


def opportunity_detail(request, db, oid):
    row = portfolio.opportunities(db, request.user, oid)
    fields = select_field("status", "Личный статус", portfolio.STATES.items(), row["status"] or "saved")
    fields += textarea("note", "Заметка к отклику", 1000, value=row["note"] or "")
    body = panel("Предложение", f'<p>{e(row["sponsor"])}</p><p>{e(row["description"])}</p>')
    details = practice.offer(db, request.user, oid)
    if details:
        zone = request.user["timezone"]
        content = f'<p>Длительность {details["duration_days"]} дн. · Приём заявок до {e(local_label(details["closes_at"], zone))}.</p>'
        if details["closes_at"] > timestamp():
            content += f'<p>{link("/services/practice-enrolment?practice=" + oid, "Подать заявку на демо-практику")}</p>'
        body += panel("Учебная практика", content)
    body += panel("Мой трекер", "<p>Статус обновляется вручную и сохраняется в твоём кабинете.</p>"
                  + form(request, f"/opportunities/{oid}", fields + button("Сохранить статус"), command=True))
    return page(request, row["title"], body + back("/opportunities", "К предложениям"))
