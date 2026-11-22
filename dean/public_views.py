"""Existing cabinet views for the optional public study context."""
from datetime import date, timedelta
import json
from urllib.parse import urlencode
from zoneinfo import ZoneInfo

from . import appearance, calendar_tools, calendar_views, dashboard_settings, public_schedule as catalog, public_sync, study_context, workspace
from .jobs import source_state
from .clock import now
from .domain import require, role, timestamp
from .schedule import local_value
from .ui import back, button, day_label, e, empty, form, hidden, input_field, link, local_label, page, panel, select_field, textarea

MODE = {"snapshot": "Сохранённый снимок официального расписания",
        "live": "Официальный ответ источника", "synthetic": "Синтетические данные для теста"}


def scope_label(selection):
    source_id, group_id = selection
    source = catalog.SOURCES[source_id]
    return catalog.source_label(source) + " · " + source["groups"][group_id]["label"]


def context_page(request, db):
    role(request.user, "student")
    choices = [("", "Учебное демо")]
    for source in catalog.SOURCES.values():
        choices.extend((source["source_id"] + "|" + gid, " · ".join(filter(None, (
                           catalog.source_label(source), group["department"], group["label"]))))
                       for gid, group in source["groups"].items())
    selected = "|".join(request.public) if request.public else ""
    main, revision = study_context.preference(db, request.user)
    option = study_context.option(db, request.user, request.public)
    controls = hidden("revision", revision) + select_field("selection", "Вуз и группа", choices, selected)
    controls += input_field("focus_day", "Дата просмотра", option["focus_day"] or "" if option else "", kind="date")
    controls += '<button name="action" value="view">Открыть расписание</button>'
    controls += '<button name="action" value="main">Сделать основным</button>'
    body = form(request, "/schedule/context", controls, style="inline")
    main_label = "Учебное демо" if main == study_context.DEMO else scope_label(main)
    body += f'<p>Основной контекст: {e(main_label)}.</p>'
    body += form(request, "/schedule/context/return", hidden("revision", revision)
                 + button("Вернуться к основному"))
    if selected:
        marked = option and option["favorite"]
        body += form(request, "/schedule/context/" + ("unfavorite" if marked else "favorite"),
                     hidden("revision", revision) + hidden("selection", selected)
                     + button("Убрать из избранного" if marked else "В избранное"))
    for favorite in study_context.favorites(db, request.user):
        body += form(request, "/schedule/context/view", hidden("revision", revision)
                     + hidden("selection", "|".join(favorite)) + button(scope_label(favorite)))
    body += "<p>Демо-зачисление и права в кабинете сохраняются. Для текущих источников подгруппы не подтверждены.</p>"
    body += link("/study/subjects", "Мои предметы") + " · " + link("/study/public/subjects", "Дисциплины расписания")
    links = "".join(f'<li>{link(source.get("schedule_url", source["origin"]), catalog.source_label(source))}</li>'
                    for source in catalog.SOURCES.values())
    return page(request, "Учебный контекст", panel("Расписание", body)
                + panel("Официальные страницы вузов", '<ul class="rows">' + links + "</ul>"))


def info(db, selection, day):
    source_id, group_id = selection
    source = catalog.SOURCES[source_id]
    group = source["groups"][group_id]
    marker = catalog.public_week(db, selection, day)
    links = []
    published = {row[0] for row in db.execute("""
        SELECT monday FROM public_week WHERE source_id=? AND group_id=? AND revision>0
    """, selection)}
    for monday in sorted(published | group["weeks"].keys()):
        url = "/schedule?" + urlencode({"view": "week", "date": monday})
        links.append(link(url, day_label(date.fromisoformat(monday))))
    lines = list(filter(None, (scope_label(selection), group["department"],
                               " ".join(filter(None, (source["term"], source["academic_year"]))))))
    if marker:
        week = catalog.week_scope(*selection, marker["monday"])
        if "number" in week.info:
            lines.append(f'{week.info["number"]}-я неделя · {week.info["parity"]}')
        elif week.info.get("parity"):
            lines.append(week.info["parity"].capitalize() + " неделя")
        if week.info.get("source_updated_at"):
            lines.append("Дата обновления на сайте вуза " + week.info["source_updated_at"])
        lines.append(MODE[marker["provenance"]] + " · прочитано " + marker["observed_at"])
        if source.get("sample_file") and marker["provenance"] == "snapshot":
            lines.append("В календаре доступна проверенная выборка занятий.")
        source_link = link(week.url, "Официальная неделя")
    else:
        lines.append("Данные на этот период не получены.")
        source_link = link(source["origin"] + group["path"], "Официальная группа")
    if not catalog.live_available(source_id):
        lines.append("Живое обновление выключено.")
    status = "".join(f"<p>{e(line)}</p>" for line in lines)
    nav = '<div class="switcher">' + " ".join([*links, source_link, link("/schedule/context", "Сменить группу")]) + "</div>"
    return panel("Источник расписания", status + nav, style="rw-source") + pattern_panel(selection)


def pattern_panel(selection, room_link=None):
    source = catalog.SOURCES[selection[0]]
    group = source["groups"][selection[1]]
    records = group.get("pattern")
    if not records:
        return ""
    stamp = group.get("source_updated_at")
    meta = (f'<p>Выборка из сетки группы · прочитано {e(source["observed_at"])}. '
            'Привязка к датам пока не подтверждена.</p>')
    if stamp:
        meta += f"<p>Дата сборки на сайте {e(stamp)}.</p>"
    rows = []
    for entry in records:
        details = " · ".join(filter(None, (entry["day"], f'{entry["start"]}–{entry["end"]}',
                                          entry.get("kind"), entry.get("room"), entry.get("rule"))))
        room = ""
        if entry.get("room_ref"):
            href = room_link(entry["room_ref"]) if room_link else room_href("ref:" + entry["room_ref"])
            room = f'<p>{link(href, "Подробнее о помещении")}</p>'
        rows.append(f'<li><div><strong>{e(entry["subject"])}</strong><p class="meta">{e(details)}</p>{room}</div></li>')
    return panel("Справочная сетка группы", meta + '<ul class="rows">' + "".join(rows) + "</ul>",
                 link(source["origin"] + group["path"], "Полная сетка на сайте вуза"))


def today(request, db, today_body, today_caption, today_lead):
    selection = request.public
    period = catalog.scope(selection)
    day = now().astimezone(ZoneInfo(period.timezone)).date()
    lessons = [row for row in calendar_tools.calendar(db, request.user, selection, day, day + timedelta(days=1))
               if row.kind in {"lesson","exam"}]
    covered = bool(lessons) or catalog.covered_day(selection, day, catalog.public_week(db, selection, day))
    empty_agenda = "Сегодня занятий нет." if covered else "Данные расписания на сегодня не получены."
    tasks = workspace.tasks(db, request.user, state="todo")[:4]
    current = timestamp()
    nearest = [row for row in lessons if row.end and row.end > current]
    if not nearest:
        nearest = [row for row in calendar_tools.calendar(db, request.user, selection, day + timedelta(days=1),
                                                        day + timedelta(days=8)) if row.kind == "lesson"]
    settings = dashboard_settings.preferences(db, request.user, "public")
    lead = today_lead(nearest[0], period.timezone, day, current) if nearest else ""
    panels, actions, _ = settings
    priority = {}
    task = next((row for row in tasks if row["due_at"]), None) if "tasks" in panels else None
    if task:
        time = local_label(task["due_at"], request.user["timezone"])
        caption = "Просрочено" if task["due_at"] < current else "Срок"
        priority["tasks"] = appearance.priority_link(f'/tasks/{task["id"]}', task["title"], f'{caption} · {time}', "tasks")
    body = today_body(db, request.user, zone=period.timezone, lead=lead,
                      lead_href=nearest[0].href if nearest else "", priority=priority,
                      timeline=lessons, actions=actions, empty_agenda=empty_agenda)
    change_count = sum(entry.changed for entry in lessons)
    subtitle = today_caption(day, len(lessons), change_count)
    from .ai_views import chat
    from .ui import calendar_heading
    return page(request, "Сегодня", body, subtitle, section="today", context_group=scope_label(selection),
                heading_action=appearance.today_clock(period.timezone, "public"),
                heading_detail=calendar_heading(day, len(lessons), change_count),
                dashboard_chat=chat(request, db, embedded=True))


def room_href(path):
    record = catalog.ROOM_REF.fullmatch(path)
    if record:
        return f"/schedule/room/{record[1]}"
    place = catalog.ROOM_PATH.fullmatch(path)
    return f"/schedule/rooms/{place[1]}/{place[2]}" if place else None


def rooms(db, selection):
    if not selection:
        return {}
    result = {path: name for row in db.execute("""
        SELECT DISTINCT rooms_json FROM public_lesson WHERE source_id=? AND group_id=?
    """, selection) for name, path in json.loads(row["rooms_json"]) if path}
    result.update(("ref:" + key, card["label"]) for key, card in
                  catalog.SOURCES[selection[0]].get("room_cards", {}).items())
    return result


def room_detail(request, db, building, number):
    selection = request.public
    path = f"/buildings/{building}/auditoriums/{number}"
    label = require(rooms(db, selection).get(path))
    source = catalog.SOURCES[selection[0]]
    building_info = source.get("buildings", {}).get(building)
    body = link(source["origin"] + path, "Расписание аудитории на сайте вуза")
    if building_info:
        body += "<p>" + e(building_info["name"]) + "</p>"
        if building_info.get("address"):
            body += "<p>" + e(building_info["address"]) + "</p>"
        body += "<p>" + link(building_info["source_url"], "Официальный список корпусов") + "</p>"
    if not building_info or not building_info.get("address"):
        body += empty("Адрес корпуса пока не подтверждён.")
    return page(request, f"Аудитория {label}", back("/schedule", "К расписанию") + panel("Помещение", body),
                "Публичный справочник помещений")


def reference_room(request, db, key):
    selection = request.public
    source = catalog.SOURCES[selection[0]] if selection else {}
    card = require(source.get("room_cards", {}).get(key))
    details = f'<p>{e(card["detail"])}</p>' if card.get("detail") else empty("Обозначение из расписания группы.")
    details += link(card["url"], "Официальный источник")
    return page(request, card["label"], back("/student-services", "К справочнику") + panel("Помещение", details),
                scope_label(selection))


def lesson_detail(request, db, event_id):
    selection = request.public
    row = catalog.local_lesson(db, selection, event_id)
    period = catalog.scope(selection)
    week = catalog.week_scope(*selection, row["monday"])
    lesson = catalog.Provider(db, selection).to_lesson(period, row)
    date_text = local_label(lesson.starts_at, period.timezone)
    time_end = local_label(lesson.ends_at, period.timezone).rsplit(" ", 1)[-1]
    status = "Отменено источником" if row["cancelled"] else row["kind"]
    details = (f"<p>{e(date_text)}–{e(time_end)} · {e(status)}</p>"
               "<p>Имена преподавателей исключены из конкурсной версии.</p>"
               f'<p>{e(MODE[row["provenance"]])} · прочитано {e(row["observed_at"])}</p>'
               f'<p>{link(week.url, "Официальное расписание группы")}</p>'
               f'<p>{link("/search?" + urlencode({"q": lesson.subject, "kind": "subject"}), "Предмет в расписании")}</p>')
    from .public_subjects import subject_href
    if not row["cancelled"]:
        details += "<p>" + link(subject_href(selection, lesson.subject), "Карточка дисциплины") + "</p>"
    places = json.loads(row["rooms_json"])
    place_items = []
    for name, path in places:
        href = room_href(path)
        place_items.append(f"<li>{link(href, name) if href else e(name)}</li>")
    room_links = ("<ul>" + "".join(place_items) + "</ul>" if place_items
                  else empty("Аудитория не указана или отсутствует в проверенных полях."))
    saved = catalog.note(db, request.user, event_id)
    note_field = textarea("body", "Моя заметка", 1600, value=saved["body"] if saved else "")
    status_text = '<p class="meta" role="status">Личная запись сохранена.</p>' if saved else ""
    checked = ' checked' if saved and saved["remind"] else ''
    opt = f'<label class="check"><input type="checkbox" name="remind" value="1"{checked}>Напомнить за 30 минут</label>'
    note = form(request, f"/schedule/public/{event_id}/note", note_field + opt + button("Сохранить"), command=True)
    prev = calendar_tools.lesson_schedule(request, local_value(lesson.starts_at, period.timezone).date(), "week")
    body = back(prev, "К расписанию") + panel("Занятие", details) + panel("Аудитории", room_links)
    body += panel("Личная запись", status_text + note)
    body += calendar_views.override_panel(request, db, row, period.timezone)
    return page(request, lesson.subject, body, scope_label(selection))


def service_panels(selection):
    source = catalog.SOURCES[selection[0]]
    body = ""
    for service in source["services"]:
        facts = f'<p>{e(service["address"])}</p>' if service.get("address") else ""
        body += f'<li><div>{link("/study/public/services/" + service["id"], service["label"])}{facts}' 
        body += f'<p>{link(service["url"], "Официальный сайт")}</p></div></li>'
    for key, card in source.get("room_cards", {}).items():
        body += f'<li>{link(room_href("ref:" + key), card["label"])}</li>'
    return panel("Справочные сервисы", f'<ul class="rows">{body}</ul>')


def services_page(request, db):
    role(request.user, "student")
    return page(request, "Сервисы", service_panels(request.public)
                + back("/student-services?mode=demo", "Демо-сервисы кампуса"), scope_label(request.public))


def review_list(request, db):
    public_sync.require_operator(db, request.user)
    request.railway_operator = True
    choices = []
    for source in catalog.SOURCES.values():
        for group_id, group in source["groups"].items():
            for week in catalog.known_weeks(source["source_id"], group_id):
                value = "|".join((source["source_id"], group_id, week))
                mode = " · только запрос к источнику" if week not in group["weeks"] else ""
                choices.append((value, f'{catalog.source_label(source)} · {group["label"]} · {week}{mode}'))
    control = select_field("scope", "Группа и неделя", choices)
    control += select_field("mode", "Получение", [("snapshot", "Сохранённый снимок"), ("live", "Живой канал")])
    control += button("Сравнить")
    body = panel("Сравнение перед публикацией", form(request, "/staff/public", control, style="inline"), style="rw-source")
    rows = db.execute("SELECT * FROM public_preview ORDER BY rowid DESC LIMIT 15")
    previews = "".join(f'<li><div>{link(f"/staff/public/{row["id"]}", row["group_id"] + " · " + row["monday"])}'
                       f'<p>{e(MODE[row["provenance"]])} · {"Применено" if row["applied_at"] else "Ожидает проверки"}'
                       f'</p></div></li>' for row in rows)
    attempts = "".join(
        f'<li><div>{e(row["group_id"])} · {e(row["monday"])}<p>{e(row["started_at"])} · {e(row["result"])}</p></div></li>'
        for row in db.execute("SELECT * FROM public_attempt ORDER BY rowid DESC LIMIT 12"))
    status = ""
    for source_id in catalog.SOURCES:
        record = source_state(db, source_id)
        mode = {"saved_snapshot":"Сохранённый снимок", "reference_grid":"Справочная сетка", "link":"Ссылка"}
        live = "Живой канал разрешён" if record["live_enabled"] else "Живой канал выключен"
        extra = " · Локальный адаптер проверяется на фикстурах" if record["adapter_local"] else ""
        facts = [
            live + extra,
            f'Опубликованных недель: {record["weeks"]}',
            f'Период: {record["from_week"]} — {record["through_week"]}' if record["from_week"] else "Датированный охват не опубликован",
            f'Чтение содержимого: {record["content_read_at"] or "нет"}',
            f'Последняя публикация: {record["last_publication"] or "нет"}',
            f'Последнее успешное получение: {record["last_successful_fetch"] or "нет"}',
        ]
        if record["scheduled"]:
            facts.append("Автопроверка настроена через команду public-refresh.")
        status += f'<li><div><strong>{e(catalog.source_label(catalog.SOURCES[source_id]))} · {mode[record["configured_mode"]]}</strong>'
        status += "".join(f"<p>{e(line)}</p>" for line in facts) + "</div></li>"
    body += panel("Охват источников", f'<ul class="rows">{status}</ul>')
    body += panel("Сравнения", f'<ul class="rows">{previews}</ul>' if previews else empty("Сравнений пока нет."))
    body += panel("Попытки обновления", f'<ul class="rows">{attempts}</ul>' if attempts else empty("Попыток пока нет."))
    return page(request, "Открытые источники", body)


def review(request, db, key):
    public_sync.require_operator(db, request.user)
    request.railway_operator = True
    preview = public_sync.read_preview(db, key)
    week = catalog.week_scope(preview["source_id"], preview["group_id"], preview["monday"])
    diff = public_sync.differences(db, preview)
    labels = {"added": "Новые", "changed": "Изменения", "cancelled": "Отмены", "ambiguous": "Неоднозначные",
              "missing": "Отсутствуют в ответе · сохранены"}
    lines = []
    for group, records in diff.items():
        descriptions = []
        for item in records:
            event = item if group == "missing" else item[0]
            descriptions.append("<li>" + e(event["subject"]) + " · " + e(event["starts_at"]) + "</li>")
        lines.append(f'<li><strong>{e(labels[group])} · {len(records)}</strong><ul>{"".join(descriptions)}</ul></li>')
    body = back("/staff/public", "К открытым источникам")
    body += '<p>' + e(MODE[preview["provenance"]]) + " · " + e(preview["observed_at"]) + "</p>"
    body += panel("Предпросмотр недели", '<ul class="rows">' + "".join(lines) + "</ul>",
                  link(week.url, "Сверить с источником"))
    restoring = public_sync.rollback_info(db, key)
    if restoring:
        body += f'<p>Откат к {link("/staff/public/" + restoring["restore_preview_id"], "предыдущей публикации")}.</p>'
    if preview["applied_at"]:
        body += '<p role="status">Сравнение опубликовано.</p>'
        body += form(request, f"/staff/public/{key}/rollback", button("Подготовить откат к этому содержимому"))
    elif not (diff["ambiguous"] or diff["missing"]):
        body += form(request, f"/staff/public/{key}", button("Подтвердить публикацию"))
    else:
        body += empty("Сверь неполный или неоднозначный ответ с источником.")
    return page(request, "Сравнение расписания", body, scope_label((week.source_id, week.group_id)))
