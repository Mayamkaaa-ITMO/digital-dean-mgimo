import json
from datetime import date, timedelta
from urllib.parse import urlencode
from zoneinfo import ZoneInfo

from .domain import DomainError, require, timestamp
from .clock import now
from .db import read_snapshot
from .schedule import local_value, staff_lessons, student_scope, today_for
from . import applications as appl, coursework, learning, services as svc
from . import agenda, appearance, application_forms, attachment, attendance, calendar_tools, campus, dashboard_settings, finances, profile_tools, reminders, retakes, search, study_views, workspace
from . import ai_views, public_schedule, public_sync, public_views, schedule_display, study_context
from .pdf import document_text
from .schedule import local_midnight
from .static_assets import asset_url
from .ui import (SHORT_DAYS, WEEKDAYS, back, button, calendar_heading, day_label, e, empty, form, hidden, input_field,
                 link, local_label, page, panel, select_field, textarea)

def error(request, status, message, *, recovery=""):
    titles = {400: "Некорректный запрос", 403: "Доступ ограничен", 404: "Страница недоступна",
              405: "Действие недоступно", 409: "Данные изменились", 413: "Запрос слишком большой",
              422: "Проверь данные", 429: "Слишком много попыток", 500: "Ошибка сервера",
              503: "База временно недоступна"}
    if request.user and request.user["role"] == "staff":
        home, caption = "/staff", "В кабинет сотрудника"
    elif request.user:
        home, caption = "/", "В мой кабинет"
    else:
        home, caption = "/login", "К входу"
    title = titles.get(status, "Ошибка")
    action = ""
    if recovery:
        title = "Форму нужно обновить" if request.user else "Сеанс завершён"
        if recovery != home:
            action = back(recovery, "Открыть форму заново" if request.user else "Войти и открыть форму")
    return page(request, title, f'<p role="alert">{e(message)}</p>{action}{back(home, caption)}')


def timeline_time(when, label):
    if "–" not in label:
        return f'<time datetime="{e(when)}">{e(label)}</time>'
    start, stop = label.split("–", 1)
    return (f'<time datetime="{e(when)}"><span class="legacy-only">{e(label)}</span>'
            f'<span class="rw-only">{e(start)}<small>до {e(stop)}</small></span></time>')


def urgent_application(db, user):
    applications = appl.applications(db, user, status="needs_changes")
    if not applications:
        return None
    app = applications[0]
    note = db.execute("""
        SELECT note FROM application_history WHERE application_id=? AND status='needs_changes' AND event_kind='status'
        ORDER BY id DESC LIMIT 1
    """, (app["id"],)).fetchone()
    message = note["note"] if note and note["note"] else "Открой заявление и проверь замечания."
    return f'/requests/{app["id"]}', app["service_name"], message


def date_strip(focus, actual, href, *, forward=False, active_days=()):
    first = focus if forward else focus - timedelta(days=focus.weekday())
    days = (first + timedelta(days=offset) for offset in range(7))
    items = []
    for day in days:
        weekday = WEEKDAYS[day.weekday()]
        current = ' aria-current="date"' if day == focus else ""
        today = ' class="rw-actual-day"' if day == actual else ""
        marker = ' data-scheduled' if day in active_days else ""
        label = f"{weekday}, {day_label(day)}" + (", сегодня" if day == actual else "")
        compact = (f'<span class="rw-v29-only">{"Сегодня" if day == actual else f"{SHORT_DAYS[day.weekday()]} {day.day}"}</span>'
                   if forward else "")
        digits = (f'<span>{SHORT_DAYS[day.weekday()]}</span><time datetime="{day.isoformat()}">{day.day}</time>')
        items.append(f'<a href="{e(href(day))}" aria-label="{e(label)}"{current}{today}{marker}>{compact}'
                     f'<span class="rw-date-parts">{digits}</span></a>')
    return '<nav class="rw-date-strip rw-only" aria-label="Дни недели">' + "".join(items) + "</nav>"


def today_timeline(events, zone, previous_rooms, following_id):
    rows = []
    for item in events:
        start = local_value(item.start, zone)
        stop = local_value(item.end, zone) if item.end else None
        end = stop.strftime("%H:%M" if start.date() == stop.date() else "%d.%m %H:%M") if stop else ""
        time_range = f"{start:%H:%M}" + (f"–{end}" if end else "")
        kind = item.lesson_kind or "Занятие"
        old_room = previous_rooms.get(item.key)
        change = "Перенос аудитории" if old_room else "Занятие изменено" if item.changed else ""
        timeline_room = f"Перенос в {item.room}" if old_room and item.room else item.room
        timeline_notes = " · ".join(filter(None, (
            time_range, timeline_room, item.teacher, item.lesson_status, "Следующая пара" if item.key == following_id else "",
        )))
        preview = schedule_display.preview_attributes(
            item, start, time_range, kind, tuple(filter(None, (change, item.lesson_status))), old_room=old_room or "",
        )
        rows.append(f'<li><a href="{e(item.href)}"{preview}>'
                    f'<span class="dean-agenda-icon" data-agenda-kind="{e(kind)}">{appearance.icon("calendar")}</span>'
                    f'<span class="dean-agenda-description"><strong>{e(item.title)}</strong><small>{e(timeline_notes)}</small>'
                    f'</span>{appearance.icon("chevron-right")}</a></li>')
    return '<ol class="dean-agenda-list">' + "".join(rows) + '</ol>'


def today_lead(item, zone, today, current, *, old_room=""):
    start = local_value(item.start, zone)
    stop = local_value(item.end, zone)
    begin = f"{start:%H:%M}"
    end = stop.strftime("%H:%M" if stop.date() == start.date() else "%d.%m %H:%M")
    kind = item.lesson_kind or "Занятие"
    caption = "Идёт сейчас" if item.start <= current else "Ближайшая пара"
    if start.date() != today:
        caption = "Следующая пара · " + day_label(start.date())
    change = "Перенос аудитории" if old_room else "Занятие изменено" if item.changed else ""
    preview = schedule_display.preview_attributes(
        item, start, f"{begin}–{end}", kind, tuple(filter(None, (change, item.lesson_status))), old_room=old_room,
    )
    return appearance.lesson_lead(caption, item.href, item.title, begin, end, kind, item.room, item.changed,
                                  note=item.lesson_status, old_room=old_room, preview=preview)


def lesson_revision(db, university_id, event_id, version):
    return db.execute("""
        SELECT old_starts_at, old_ends_at, old_room
        FROM schedule_revision WHERE university_id = ? AND event_id = ? AND version = ?
    """, (university_id, event_id, version)).fetchone()


def today_caption(day, lesson_count, change_count):
    count = appearance.count_label(lesson_count, "пара", "пары", "пар")
    changes = appearance.count_label(change_count, "перенос", "переноса", "переносов")
    return f"{WEEKDAYS[day.weekday()]}, {day_label(day)} · {count}" + (f" · {changes}" if change_count else "")


def today_body(db, user, *, zone, lead="", lead_href="", priority=None, timeline=(), following_id="",
               previous_rooms=None, actions=(), empty_agenda="Сегодня занятий нет.", agenda_note=""):
    other_lessons = [item for item in timeline if item.href != lead_href]
    today = ""
    if other_lessons or not timeline:
        rows = today_timeline(other_lessons, zone, previous_rooms or {}, following_id) if other_lessons else empty(empty_agenda)
        if agenda_note:
            rows = f'<p class="meta">{e(agenda_note)}</p>' + rows
        heading = "Остальные пары сегодня" if len(other_lessons) < len(timeline) else "Пары сегодня"
        today = panel(heading, rows, link("/schedule", "Всё расписание"), style="dean-today-agenda")
    feature = f'<div class="dean-today-feature">{lead}</div>' if lead else ""
    urgent = urgent_application(db, user)
    priority = priority or {}
    urgent_card = ""
    if urgent:
        href, service, message = urgent
        urgent_card = panel("Заявление на доработке", f'<p>{e(service)} · {e(message)}</p>',
                            link(href, "Исправить"), style="dean-today-urgent")
    actions_panel = panel("Нужно сделать", "".join(priority.values()), style="rw-priority") if priority else ""
    shortcuts = ""
    quick_links = [dashboard_settings.SHORTCUTS[key] for key in actions if key in dashboard_settings.SHORTCUTS]
    if quick_links:
        links = "".join(f'<a href="{e(href)}"><span class="dean-subject-icon">{appearance.icon("book" if href == "/grades" else "tasks")}</span>'
                        f'<strong>{e(label)}</strong></a>' for href, label in quick_links)
        shortcuts = f'<section class="dean-quick-actions"><h2>Быстрые действия</h2><div>{links}</div></section>'
    return f'{feature}{shortcuts}{today}{urgent_card}{actions_panel}'


def dashboard(request, db, provider):
    user = request.user
    if request.public:
        return public_views.today(request, db, today_body, today_caption, today_lead)
    scope = student_scope(user)
    zone = scope.timezone
    today = today_for(user)
    current = timestamp()
    end = local_midnight(today + timedelta(days=1), zone)
    lessons = provider.occurrences(scope, today, today + timedelta(days=1))
    identity = svc.profile(db, user)
    ongoing = [lesson for lesson in lessons if lesson.starts_at <= current < lesson.ends_at]
    remaining = [lesson for lesson in lessons if lesson.starts_at > current]
    active = svc.results(db, user)
    upcoming = sorted((row for row in active if row["exam_at"] and row["score"] is None
                       and row["outcome"] is None and row["exam_at"] >= current), key=lambda row: row["exam_at"])
    next_item = upcoming[0] if upcoming else None
    tasks = workspace.tasks(db, user, state="todo")
    overdue = [row for row in tasks if row["due_at"] and row["due_at"] < current][:4]
    due_today = [row for row in tasks if row["due_at"] and current <= row["due_at"] < end][:4]
    docs = appl.documents(db, user)[:4]
    next_lessons = (ongoing or remaining)[:1]
    if not next_lessons:
        next_lessons = provider.occurrences(scope, today + timedelta(days=1), today + timedelta(days=8))[:1]
    lead = ""
    lead_href = ""
    if next_lessons:
        lesson = next_lessons[0]
        revision = lesson_revision(db, lesson.university_id, lesson.id, lesson.version) if lesson.version > 1 else None
        old_room = ""
        if revision and revision["old_room"] != lesson.room:
            old_room = revision["old_room"] or "Аудитория не указана"
        entry = agenda.Entry(lesson.id, lesson.subject, lesson.starts_at, lesson.ends_at, "lesson",
                             f"/schedule/{lesson.id}", changed=lesson.version > 1, lesson_kind=lesson.kind,
                             room=lesson.room, teacher=lesson.teacher)
        lead = today_lead(entry, zone, today, current, old_room=old_room)
        lead_href = entry.href
    settings = dashboard_settings.preferences(db, user, "demo")
    ordered, actions, _ = settings
    priority = {}
    for key, task_group in (("tasks", due_today), ("overdue", overdue)):
        if task_group:
            task = task_group[0]
            time = local_value(task["due_at"], zone)
            priority[key] = appearance.priority_link(f'/tasks/{task["id"]}', task["title"],
                                                     f'Сегодня до {time:%H:%M}' if key == "tasks" else
                                                     f'Просрочено · {time:%d.%m.%Y, %H:%M}', "tasks")
    if docs:
        doc = docs[0]
        priority["documents"] = appearance.priority_link(f'/requests/{doc["application_id"]}#request-document',
                                                         doc["title"], "Готовый PDF", "file")
    if next_item:
        priority["exam"] = appearance.priority_link(f'/grades/{next_item["id"]}', next_item["subject"],
                                                    "Экзамен · " + local_label(next_item["exam_at"], zone), "calendar")
    timeline = []
    previous_rooms = {}
    for lesson in lessons:
        timeline.append(agenda.Entry(lesson.id, lesson.subject, lesson.starts_at, lesson.ends_at, "lesson",
                                     f"/schedule/{lesson.id}", changed=lesson.version > 1, lesson_kind=lesson.kind,
                                     room=lesson.room, teacher=lesson.teacher))
        revision = lesson_revision(db, lesson.university_id, lesson.id, lesson.version) if lesson.version > 1 else None
        if revision and revision["old_room"] != lesson.room:
            previous_rooms[lesson.id] = revision["old_room"] or "Аудитория не указана"
    body = today_body(db, user, zone=zone, lead=lead, lead_href=lead_href,
                      following_id=remaining[0].id if ongoing and remaining else "",
                      priority={key: priority[key] for key in ordered if key in priority}, timeline=timeline,
                      previous_rooms=previous_rooms, actions=actions,
                      agenda_note="На сегодня занятия закончились." if lessons and not (ongoing or remaining) else "")
    change_count = sum(lesson.version > 1 for lesson in lessons)
    subtitle = today_caption(today, len(lessons), change_count)
    group = identity["group_name"]
    return page(request, "Сегодня", body, subtitle, section="today", context_group=group,
                heading_action=appearance.today_clock(zone, "demo"),
                heading_detail=calendar_heading(today, len(lessons), change_count),
                dashboard_chat=ai_views.chat(request, db, embedded=True))


def profile(request, db, *, photo_error=""):
    row = svc.profile(db, request.user)
    labels = (("ФИО", "full_name"), ("Институт", "faculty"), ("Направление", "program_name"),
              ("Курс", "course_year"), ("Группа", "group_name"), ("Семестр", "semester"))
    body = '<dl class="facts panel">' + "".join(
        f'<div><dt>{label}</dt><dd>{e(row[key])}</dd></div>' for label, key in labels
    ) + "</dl>"
    if request.user["role"] == "student":
        exists = db.execute("SELECT 1 FROM profile_photo WHERE owner_id=?", (request.user["id"],)).fetchone()
        picture = ('<img src="/profile/photo" width="68" height="68" alt="Фото профиля">' if exists else
                   appearance.icon("profile"))
        feedback = f'<p role="alert" class="error">{e(photo_error)}</p>' if photo_error else ""
        upload = (feedback + '<label>JPEG или PNG до 20 МиБ'
                  '<input type="file" name="file" accept="image/jpeg,image/png" aria-describedby="profile-upload-status" required></label>'
                  '<p class="error" id="profile-upload-status" role="alert" hidden></p>'
                  + button("Обновить фото" if exists else "Добавить фото"))
        removal = form(request, "/profile/photo/remove", '<button class="quiet">Удалить фото</button>') if exists else ""
        picture_form = form(request, "/profile/photo", upload, multipart=True, style="dean-profile-upload")
        body = panel("Фото профиля", f'<div class="dean-profile-photo">{picture}'
                     f'<div>{picture_form}{removal}</div></div>') + body
    actions = [("/profile/access", "Способы входа"), ("/profile/sessions", "Активные сеансы и устройства"), ("/profile/privacy", "Приватность"),
               ("/profile/links", "Сохранённые ссылки"), ("/profile/data", "Резервная копия"), ("/profile/dashboard", "Настройки главной"),
               ("/profile/telegram", "Telegram"), ("/onboarding", "Первые шаги"), ("/support", "Поддержка")]
    body += '<div class="toolbar">' + " ".join(link(path, label) for path,label in actions) + "</div>"
    return page(request, "Профиль", body, section="profile")


def all_services(request, db):
    workspace.scope(request.user)
    from . import campus_directory, campus_lab, campus_pay, demo_views, room_occupancy

    query = request.query("q").strip()[:120]
    words = query.casefold().replace("ё", "е").split()

    def found(*labels):
        text = " ".join(labels).casefold().replace("ё", "е")
        return all(word in text for word in words)

    occupancy = room_occupancy.available(request.user)
    has_pass = demo_views.campus_pass_available(request)
    occupancy_tile = (room_occupancy.PATH, room_occupancy.LABEL, "building", "Свободные места и тепловая карта")
    campus = {
        "booking": ("/bookings", "Бронирования", "calendar", "Найти место или записаться"),
        "map": ("/campus-map", "Карта кампуса", "map", "Найти аудиторию"),
    }
    if occupancy:
        campus["quick"] = occupancy_tile
    elif has_pass:
        campus["quick"] = ("/campus-pass", "Демопропуск", "qr", "Показать учебный QR-код")
    library = ("/library", "Библиотека", "library", "Книги и читальные залы")
    study = {
        "requests": ("/requests?mode=catalog", "Заявки", "tasks", "Справки и обращения"),
        "documents": ("/documents", "Документы", "file", "Справки и выписки"),
        "library": library,
        "grades": ("/grades", "Успеваемость", "book", "Оценки и аттестации"),
    }
    campus_additional = (
        ("/student-services", "Справочник студенческих сервисов", "building", "сервисы кампуса"),
        ("/equipment", "Техника", "lab", ""),
        ("/residence", "Проживание", "building", ""),
        ("/universities", "Открытые расписания", "source", ""),
    )
    if campus_lab.enabled(request.user):
        campus_additional += (("/campus-lab", "Навигация и инструменты кампуса", "building", "сервисы кампуса"),)
    if has_pass and occupancy:
        campus_additional += (("/campus-pass", "Демопропуск", "qr", "пропуск учебный QR-код"),)
    campus_additional += tuple((item.PATH, item.LABEL, "building", "") for item in (campus_pay, campus_directory)
                               if item.available(request.user))
    categories = (
        ("Учёба", "study", (
            ("/lecture-library", "Лекции", "book", "Видеотека и видео"),
            ("/curriculum", "Учебный план", "file", ""),
            ("/retakes", "Пересдачи", "tasks", ""),
            ("/work", "Учебные работы", "tasks", ""),
            ("/study/subjects", "Мои предметы", "book", ""),
            ("/courses", "Курсы", "book", ""),
        )),
        ("Ещё сервисы", "tools", (
            ("/finance", "Финансы", "file", ""),
            ("/activities", "Афиша", "calendar", "события"),
            ("/tasks", "Задачи", "tasks", ""),
        )),
        ("Другие сервисы кампуса", "campus", campus_additional),
        ("Практика и события", "tools", (
            ("/opportunities", "Практики и проекты", "tasks", ""),
            ("/practice", "Дневник практики", "book", ""),
            ("/teams", "Совместная учёба", "profile", ""),
            ("/clubs", "Клубы", "profile", ""),
            ("/portfolio", "Портфолио", "file", ""),
        )),
        ("Личные инструменты", "tools", (
            ("/study/preparation", "План подготовки", "book", ""),
            ("/study/timer", "Учебный таймер", "today", ""),
            ("/calendar/import", "Импорт календаря", "calendar", ""),
            ("/calendar/export", "Экспорт календаря", "calendar", ""),
            ("/support", "Поддержка", "help", ""),
        )),
    )
    if query:
        categories = (("Кампус", "campus", tuple(campus.values())),
                      ("Учёба и документы", "documents", tuple(study.values()))) + categories
    sections = []
    for title, tone, items in categories:
        rows = "".join(
            f'<li><a href="{e(href)}"><span class="dean-service-icon">{appearance.icon(glyph)}</span>'
            f'<span>{e(label)}</span>{appearance.icon("chevron-right")}</a></li>'
            for href, label, glyph, keywords in items if found(title, label, keywords)
        )
        if rows:
            sections.append(f'<section class="dean-service-group" data-tone="{tone}"><h2>{e(title)}</h2><ul>{rows}</ul></section>')
    search_box = directory_search_field("Найти сервис", query)
    reset = link("/all-services", "Сбросить поиск") if query else ""
    search = f'<form action="/all-services" method="get" class="dean-directory-search">{search_box}{reset}</form>'
    groups = f'<div class="dean-service-groups">{"".join(sections)}</div>'
    if query:
        results = groups if sections else empty("Сервисы не найдены. Попробуй другое название.")
    else:
        def card(key, item):
            href, label, glyph, summary = item
            action = (f'<span class="dean-service-open">Открыть {appearance.icon("arrow")}</span>'
                      if key == "booking" else "")
            return (f'<a class="dean-service-card" data-service="{key}" href="{e(href)}">'
                    f'<span class="dean-service-mark">{appearance.icon(glyph)}</span>'
                    f'<div><strong>{e(label)}</strong><span>{e(summary)}</span>{action}</div></a>')

        tiles = ['<h2 data-service="heading-campus">Кампус</h2>', card('booking', campus['booking'])]
        if "quick" in campus:
            tiles.append(card('quick', campus['quick']))
        tiles += [card('map', campus['map']), card('library', library),
                  '<h2 data-service="heading-study">Учёба и документы</h2>']
        tiles.extend(card(key, item) for key, item in study.items() if key != 'library')
        extras = link("/lecture-library", "Лекции") + link("/teams", "Совместная учёба")
        if has_pass and occupancy:
            extras += link("/campus-pass", "Демопропуск")
        results = (f'<div class="dean-service-board">{"".join(tiles)}</div><nav class="dean-service-extra" '
                   f'aria-label="Быстрые переходы">{extras}</nav>'
                   f'<details class="dean-other-services"><summary>Все сервисы</summary>{groups}</details>')
    return page(request, "Сервисы", search + results, section="services")


def get_date(value, fallback):
    if not value:
        return fallback
    try:
        day = date.fromisoformat(value)
        if 1900 <= day.year <= 9900:
            return day
    except ValueError:
        pass
    raise DomainError(422, "Выбери корректную дату расписания.")


def schedule(request, db, provider):
    selection = request.public
    scope = public_schedule.scope(selection) if selection else student_scope(request.user)
    if selection:
        provider = public_schedule.Provider(db, selection)
    today = now().astimezone(ZoneInfo(scope.timezone)).date()
    zone_label = "МСК" if scope.timezone == "Europe/Moscow" else scope.timezone
    saved_day, saved_view = calendar_tools.position(db, request.user, selection, today)
    display = request.query("view") or saved_view
    if display not in calendar_tools.SCHEDULE_VIEWS:
        raise DomainError(422, "Выбери день, неделю, список или месяц.")
    focus = get_date(request.query("date"), saved_day)
    week_start = focus - timedelta(days=focus.weekday())
    start = week_start if display == "week" else focus
    stop = start + timedelta(days=7 if display in {"week", "list"} else 1)
    previous = start - (stop-start)
    if display == "month":
        start = focus.replace(day=1)
        stop = (start + timedelta(days=32)).replace(day=1)
        previous = (start - timedelta(days=1)).replace(day=1)
    kind, subject, visibility = calendar_tools.filters(db, request.user, selection)
    kind = request.query("kind", kind)
    subject = request.query("subject", subject)
    visibility = request.query("visibility") or visibility
    if visibility not in calendar_tools.VISIBILITY:
        raise DomainError(422, "Выбери, какие календари показать.")
    load_start = week_start if display in {"day", "list"} else start
    load_stop = max(stop, week_start + timedelta(days=7)) if display == "day" else stop
    calendar = agenda.entries(db, request.user, provider, load_start, load_stop, selection)
    overlapping = agenda.conflicts(calendar)
    chosen = [item for item in calendar if (not kind or item.kind == kind)
              and (not subject or item.subject_id == subject) and calendar_tools.visible(item.kind, visibility)]

    def url(view, day):
        return "/schedule?" + calendar_tools.schedule_query(day, view, kind, subject, visibility)

    context = calendar_tools.schedule_query(focus, display, kind, subject, visibility)

    period_title = schedule_display.period_label(start, stop - timedelta(days=1))
    tabs = []
    for view, label in (("day", "День"), ("week", "Неделя"), ("list", "Список"), ("month", "Месяц")):
        active = ' aria-current="page"' if display == view else ""
        tabs.append(f'<a href="{e(url(view, focus))}" data-calendar-view="{view}"{active}>{label}</a>')
    periods = f'<nav class="switcher rw-period-tabs" aria-label="Период расписания">{"".join(tabs)}</nav>'
    subjects = [("", "Все дисциплины")]
    if selection:
        subjects.extend((row[0], row[0]) for row in db.execute("""
            SELECT DISTINCT subject FROM public_lesson WHERE source_id=? AND group_id=? AND cancelled=0
            ORDER BY subject
        """, selection))
    else:
        subjects.extend((row["id"], row["name"]) for row in workspace.subjects(db, request.user))
    view_fields = hidden("view", display) + hidden("date", focus.isoformat())
    view_fields += hidden("context_revision", study_context.preference(db, request.user)[1])
    filters = view_fields
    filters += select_field("kind", "Тип события", [("", "Все события"), *agenda.TYPES.items()], kind)
    filters += select_field("subject", "Дисциплина", subjects, subject)
    filters += select_field("visibility", "Календари", list(calendar_tools.VISIBILITY.items()), visibility)
    date_filter = (hidden("view", display) + hidden("kind", kind) + hidden("subject", subject) + hidden("visibility", visibility)
                   + input_field("date", "Дата", focus.isoformat(), "required", "date"))
    arrows = []
    for day, label, glyph in ((previous, "Предыдущий период", "chevron-left"), (stop, "Следующий период", "chevron-right")):
        arrows.append(f'<a class="dean-schedule-arrow" href="{e(url(display, day))}" aria-label="{label}" title="{label}">'
                      f'{appearance.icon(glyph)}<span class="legacy-only">{label}</span></a>')
    paging = (f'<nav class="dean-schedule-paging" aria-label="Листание расписания">'
              f'{link(url(display, today), "Сегодня", "dean-schedule-today")}{"".join(arrows)}'
              f'<strong class="dean-schedule-period">{e(period_title)}</strong></nav>')
    photo_url = "/calendar/import/photo?" + urlencode({"week": focus.isoformat()})
    extras = (("/events/new", "Добавить событие"), ("/schedule/changes", "Изменения расписания"),
              ("/schedule/free", "Свободное время"),
              ("/calendar/import", "Импорт"), ("/calendar/export", "Экспорт"),
              ("/calendar/share?"+urlencode({"week":focus.isoformat()}), "Поделиться неделей"))
    extras += (("/schedule/context", "Вуз и группа"),
               ("/schedule/raspay?" + urlencode({"date": focus.isoformat()}), "Вид Raspay"))
    tools = '<div class="dean-schedule-tools">' + " ".join(link(*item) for item in extras) + "</div>"
    tools += f'<form method="get" action="/schedule" class="inline rw-calendar-date">{date_filter}{button("Перейти")}</form>'
    tools += form(request, "/schedule/filters", filters + button("Сохранить фильтры"), style="inline")
    filter_status = ""
    filtered = bool(kind or subject or visibility != "all")
    if filtered:
        tools += "<p>Пересечения учитывают занятия и личные интервалы, в том числе события вне фильтра.</p>"
        labels = []
        if kind:
            labels.append(agenda.TYPES.get(kind, "Тип события"))
        if subject:
            labels.append(dict(subjects).get(subject, "Выбранная дисциплина"))
        if visibility != "all":
            labels.append(calendar_tools.VISIBILITY[visibility])
        badges = "".join(f'<span class="tag">{e(label)}</span>' for label in labels)
        reset = form(request, "/schedule/filters/reset", view_fields + hidden("visibility", "all")
                     + '<button class="quiet">Показать всё</button>')
        filter_status = (f'<div class="rw-active-filters" aria-label="Активные фильтры">{badges}'
                         f'{reset}</div>')
    source = ""
    if selection:
        source = f'<div id="schedule-source">{public_views.info(db, selection, focus)}</div>'
    publications = public_schedule.public_weeks(db, selection, load_start, load_stop - timedelta(days=1)) if selection else {}
    day_rows = []
    for offset in range((load_stop - load_start).days):
        day = load_start + timedelta(days=offset)
        since = local_midnight(day, scope.timezone)
        until = local_midnight(day + timedelta(days=1), scope.timezone)
        day_items = [item for item in chosen if item.start < until and (
            item.end > since if item.end else item.start >= since)]
        monday = day - timedelta(days=day.weekday())
        missing = selection and not public_schedule.covered_day(selection, day, publications.get(monday))
        day_rows.append((day, day_items, missing))
    title = f"{public_views.scope_label(selection)} · {zone_label}" if selection else zone_label
    focus_items, focus_missing = next(((items, missing) for day, items, missing in day_rows if day == focus), ([], False))
    caption = f'{day_label(focus)} · {schedule_display.day_count(focus_items, focus_missing)}'
    date_caption = f'<p>{e(caption)}</p>' if display in {"day", "list"} else ""
    heading = f'<div class="dean-schedule-heading"><h1>Расписание</h1>{date_caption}</div>'
    tools = f'<div class="dean-schedule-context">{e(title)}</div>{tools}'
    body = (f'<div class="dean-schedule-controls">{heading}{paging}{periods}'
            f'<details class="dean-schedule-actions" data-appearance-flyout data-open-in-original>'
            f'<summary aria-label="Действия расписания">{appearance.icon("calendar")}</summary>'
            f'<div class="dean-schedule-actions-body">{tools}</div></details></div>{filter_status}')
    body += (f'<div class="dean-schedule-import-tools"><a class="dean-schedule-photo" href="{e(photo_url)}">'
             f'{appearance.icon("calendar")}<span>Распознать расписание по фото</span></a></div>')
    if display in {"day", "week", "list"}:
        active_days = {day for day, items, _ in day_rows if items}
        strip_view = "list" if display == "list" else "day"
        body += date_strip(focus, today, lambda value: url(strip_view, value), active_days=active_days)
    day_rows = [row for row in day_rows if start <= row[0] < stop]

    old_rooms = {}
    if not selection:
        for item in chosen:
            if item.kind == "lesson" and item.changed:
                revision = lesson_revision(db, scope.university_id, item.key.removeprefix("lesson:"), item.version)
                if revision and revision["old_room"] != item.room:
                    old_rooms[item.key] = revision["old_room"] or "Аудитория не указана"

    def day_block(day, items, missing, *, bounds=None):
        if missing:
            message = "Данные на этот день не получены."
        else:
            message = "По выбранным фильтрам событий нет." if filtered else "На этот день занятий нет."
        return schedule_display.day_section(day, items, today, focus, url("day", day), context, scope.timezone,
                                            overlapping, message, missing, gaps=not filtered, bounds=bounds, old_rooms=old_rooms)

    extra = ""
    if display == "month":
        weekdays = "".join(f'<span class="dean-schedule-month-weekday">{label}</span>' for label in SHORT_DAYS)
        blanks = '<span aria-hidden="true"></span>' * start.weekday()
        content = weekdays + blanks + "".join(schedule_display.month_cell(day, items, today, focus, url("day", day), missing)
                                               for day, items, missing in day_rows)
        body += schedule_display.month_legend(any(missing for _, _, missing in day_rows))
        attributes = ""
    elif display == "week":
        bounds, axis = schedule_display.week_axis(((day, items) for day, items, _ in day_rows), scope.timezone)
        content = axis + "".join(day_block(day, items, missing, bounds=bounds) for day, items, missing in day_rows)
        attributes = f' data-schedule-hours="{(bounds[1] - bounds[0]) // 60}"'
    else:
        content = "".join(day_block(*row) for row in day_rows)
        attributes = ""
        if display == "day":
            extra = f'<p class="dean-schedule-later">{link(url("list", stop), "Следующие дни")}</p>'
    feed = (f'<div class="rw-calendar-days dean-schedule-feed" data-view="{display}"{attributes}>'
            f'{content}</div>')
    body += (f'<div class="dean-schedule-table-scroll">{feed}</div>' if display == "week" else feed) + extra
    if source:
        body += '<p class="rw-only rw-source-link">' + link("#schedule-source", "Источник расписания") + '</p>'
    schedule_content = f'<div class="dean-schedule-workspace">{body}{source}</div>'
    result = page(request, "Расписание", schedule_content, section="schedule")
    if (focus, display) != (saved_day, saved_view) and request.env.get("HTTP_SEC_FETCH_SITE") != "cross-site":
        calendar_tools.remember_position(db, request.user, selection, focus, display)
    return result


def lesson_detail(request, db, provider, eid):
    scope = student_scope(request.user)
    lesson = require(provider.occurrence(scope, eid))
    group = svc.profile(db, request.user)["group_name"]
    body = (f'<p>{e(lesson.kind)} · {e(group)}</p>'
            f'<p>{e(local_label(lesson.starts_at, lesson.timezone))} — '
            f'{e(local_label(lesson.ends_at, lesson.timezone))}</p>'
            f'<p>Аудитория: {e(lesson.room or "Не указана")}</p>'
            f'<p>Преподаватель: {e(lesson.teacher or "Не указан")}</p>'
            f'<p class="meta">Источник: {e("демонстрационный набор" if lesson.origin == "demo_university" else "открытое расписание")}</p>')
    from .lesson_route import banner

    day = local_value(lesson.starts_at, lesson.timezone).date()
    context = calendar_tools.lesson_context(request.query, day)
    origin = "/schedule/" + eid + ("?" + context if context else "")
    route = banner(lesson.room, origin, explain_unmapped=True)
    revision = lesson_revision(db, lesson.university_id, lesson.id, lesson.version)
    if revision:
        body += (f'<p class="change"><strong>Занятие изменено.</strong> Было: '
                 f'{e(local_label(revision["old_starts_at"], lesson.timezone))} — '
                 f'{e(local_label(revision["old_ends_at"], lesson.timezone))} · '
                 f'{e(revision["old_room"] or "аудитория не указана")}.</p>')
    room = db.execute("SELECT room_id FROM schedule_event WHERE university_id=? AND id=?",
                      (scope.university_id, eid)).fetchone()
    if room and room["room_id"]:
        repair = "/services/room-repair?" + urlencode({"room": room["room_id"]})
        body += f'<p>{link(repair, "Сообщить о неисправности в аудитории", "dean-secondary-action")}</p>'
    details = route + f'<article class="panel">{body}</article>'
    details += study_views.subject_recordings(request, db, lesson.subject_id, lesson.period_id)
    details += study_views.lesson_assignments(request, db, lesson)
    details += study_views.notes_panel(request, db, "lesson", eid)
    mark = attendance.own(db, request.user, eid)
    details += panel("Посещаемость", f'<p>{e(attendance.STATES[mark["state"]])}</p>' if mark else
                     empty("Сотрудник ещё не поставил отметку."))
    return page(request, lesson.subject, back(calendar_tools.lesson_schedule(request, day, "day"), "К расписанию") + details)


def grade_text(row, progress=""):
    if row["score"] is not None:
        return f'{row["score"]} / 100'
    if row["outcome"] is None:
        if progress == "scheduled":
            return "Экзамен предстоит" if row["kind"] == "exam" else "Зачёт предстоит"
        if progress == "awaiting":
            return "Ожидает проверки"
        return "Оценка не выставлена" if row["kind"] == "exam" else "Результат не выставлен"
    return "Зачтено" if row["outcome"] == "passed" else "Не зачтено"


def grade_items(rows, zone, states, *, link_title=True):
    if not rows:
        return empty("За этот семестр результатов пока нет.")
    items = []
    for row in rows:
        caption = "Экзамен" if row["kind"] == "exam" else "Зачёт"
        if row["exam_at"]:
            caption += f' · {local_label(row["exam_at"], zone)}'
        title = link(f"/grades/{row['id']}", row["subject"]) if link_title else e(row["subject"])
        state = states[row["id"]]
        glyph = appearance.icon("calendar" if state == "scheduled" else appearance.subject_icon(row["subject"]))
        items.append(f'<li data-grade-state="{e(state)}"><span class="dean-subject-icon">{glyph}</span>'
                     f'<div>{title}<p class="meta">{e(caption)}</p></div><strong>{e(grade_text(row, state))}</strong></li>')
    return f'<ul class="rows results dean-grade-rows">{"".join(items)}</ul>'


def grades(request, db):
    user = request.user
    available = svc.periods(db, user)
    selected = request.query("period", user["period_id"])
    if selected not in {row["id"] for row in available}:
        raise DomainError(404, "Семестр недоступен.")
    options = []
    for row in available:
        label = row["label"]
        if row["id"] == "demo-previous" and label == "Предыдущий семестр":
            start = date.fromisoformat(row["starts_on"])
            season = "Осенний" if start.month >= 8 else "Весенний"
            label = f"{season} семестр · {start.year}"
        options.append((row["id"], label))
    selector = (f'<form method="get" action="/grades" class="inline dean-grade-selector">'
                f'{select_field("period", "Семестр", options, selected)}{button("Показать")}</form>')
    rows = svc.results(db, user, selected)
    completed = sum(row["outcome"] == "passed" for row in rows)
    states = {row["id"]: learning.grade_progress(row, learning.assessments(db, row["id"])) for row in rows}
    values = tuple(states.values())
    pending, scheduled, missing = (values.count(key) for key in ("awaiting", "scheduled", "ungraded"))
    failed = sum(row["outcome"] == "failed" for row in rows)
    numeric = [row["score"] for row in rows if row["kind"] == "exam" and row["score"] is not None]
    average = ""
    if numeric:
        score = f"{sum(numeric)/len(numeric):.1f}".replace(".", ",")
        average = (f'<section class="dean-grade-summary" aria-label="Средний балл за экзамены">'
                   f'<span>Средний балл за экзамены</span><strong>{score} <small>из 100</small></strong>'
                   f'<small>Оценок в расчёте · {len(numeric)}</small></section>')
    exams = sorted((row for row in rows if row["exam_at"]), key=lambda row: row["exam_at"])
    session = grade_items(exams, user["timezone"], states) if exams else empty("В этом семестре даты экзаменов пока не указаны.")
    status = (f"Сдано: {completed} · Предстоит: {scheduled} · Ожидает проверки: {pending} · "
              f"Оценка отсутствует: {missing} · Не сдано: {failed}")
    next_exam = next((row for row in exams if row["exam_at"] > timestamp() and row["outcome"] is None), None)
    upcoming = (panel("Ближайший экзамен", f'<p>{link(f"/grades/{next_exam["id"]}", next_exam["subject"])}</p>'
                      f'<p class="meta">{e(local_label(next_exam["exam_at"], user["timezone"]))}</p>',
                      style="dean-grade-next") if next_exam else "")
    body = (f'<div class="dean-grades-grid">{selector}<div class="dean-grade-overview">{average}{upcoming}</div>'
            + '<section class="dean-grade-results"><h2>Результаты по дисциплинам</h2>'
            + grade_items(rows, user["timezone"], states) + f'<p class="meta">{e(status)}</p></section>'
            + '</div>'
            + f'<details class="dean-grade-session"><summary>Сессия и задолженности</summary>{session}'
            + f'<p>{link("/retakes", "Условия допуска и пересдачи")}</p></details>'
            + f'<p class="toolbar">{link("/work", "Мои учебные работы")} {link("/study/subjects", "Сохранённые мной предметы")}</p>')
    return page(request, "Успеваемость", body, section="grades")


def grade_detail(request, db, gid):
    user = request.user
    row = svc.results(db, user, result_id=gid)
    zone = user["timezone"]
    components = learning.assessments(db, gid)
    progress = learning.grade_progress(row, components)
    label = learning.STATES[progress]
    summary = f'<p><span class="tag">{e(label)}</span></p>' + grade_items([row], zone, {gid: progress}, link_title=False)
    body = panel("Результат", summary)
    debt = next((item for item in retakes.debts(db, user) if item["id"] == gid), None)
    if debt:
        from .reservation_views import retake_admission, retake_badge

        state, next_step = retake_admission(debt)
        body += panel("Пересдача", retake_badge(debt, state) + f'<p>{e(next_step)}</p>'
                      + link(f"/retakes/{gid}", "Открыть условия и запись", "dean-secondary-action"))
    assigned = coursework.student_list(db, user, subject_id=row["subject_id"], period_id=row["period_id"])
    body += study_views.components_panel(components, zone, assigned)
    body += study_views.subject_recordings(request, db, row["subject_id"], row["period_id"])
    if row["kind"] == "exam" and components:
        formula = "В демо итог из 100 — сумма взвешенных работ с округлением половины вверх. Для прохождения дисциплины нужно 60 из 100 итоговых баллов."
        body += panel("Правила курса", f"<p>{formula}</p><p>Ведомость изменяется сотрудником. Личный расчёт сохраняется отдельно.</p>")
    body += study_views.grade_scenario(request, db, row, components)
    body += study_views.preparation(request, db, row)
    body += study_views.grade_revisions(db, gid, zone)
    body += study_views.notes_panel(request, db, "subject", row["subject_id"])
    body += f'<p>{link("/tasks/new?" + urlencode({"subject": row["subject_id"]}), "Создать задачу по дисциплине")}</p>'
    period_url = "/grades?" + urlencode({"period": row["period_id"]})
    return page(request, row["subject"], back(period_url, "К успеваемости") + body)


def request_items(rows, zone, *, staff=False, status_filter=""):
    if not rows:
        return empty("Заявок с таким статусом нет." if status_filter else "Заявок пока нет.")
    items = []
    for row in rows:
        aid = row["id"]
        label = f'{row["service_name"]} · № {aid[:8]}'
        target = f"/requests/{aid}" + ("?" + urlencode({"status": status_filter}) if status_filter and not staff else "")
        meta = f'<p class="meta">{e(row["student_name"])} · {e(row["group_name"])}</p>' if staff else ""
        answers = appl.answers(row)
        summary = " ".join((answers.get("purpose") or answers.get("details") or "").split())
        if not staff and summary:
            meta += f'<p class="meta">{e(summary[:96])}{"…" if len(summary) > 96 else ""}</p>'
        meta += f'<p class="meta">{"Создана" if row["status"] == "draft" else "Заявка от"} {e(local_label(row["created_at"], zone))}</p>'
        items.append(f'<li><div>{link(target, label)}{meta}</div>'
                     f'{application_badge(row["status"])}</li>')
    return f'<ul class="rows results">{"".join(items)}</ul>'


def services_items(rows):
    glyphs = {"study": "book", "documents": "file", "support": "help", "campus": "building", "practice": "calendar"}
    cards = []
    for row in rows:
        category = row["category"]
        deadline = appearance.count_label(row["target_days"], "дня", "дней", "дней")
        cards.append(
            f'<li><a class="dean-service-choice" data-category="{e(category)}" href="/services/{e(row["id"])}">'
            f'<span class="dean-service-choice-meta"><span class="dean-service-choice-icon">{appearance.icon(glyphs[category])}</span>'
            f'<span>{e(appl.CATEGORIES[category])}</span><span class="dean-service-duration">до {e(deadline)}</span></span>'
            f'<span class="dean-service-choice-title"><strong>{e(row["name"])}</strong>{appearance.icon("chevron-right")}</span>'
            f'<span class="dean-service-choice-description">{e(row["description"])}</span></a></li>'
        )
    return f'<ul class="dean-service-choices">{"".join(cards)}</ul>' if cards else empty("Услуги не найдены.")


def application_badge(status):
    tones = {"needs_changes": "attention", "ready": "good", "received": "good", "completed": "good",
             "refused": "error", "cancelled": "neutral", "draft": "neutral"}
    return f'<span class="tag" data-tone="{tones.get(status, "active")}">{e(appl.STATUSES[status])}</span>'


def directory_search_field(label, value):
    return (f'<div class="dean-search-control"><button type="submit" class="quiet" aria-label="{e(label)}">'
            f'{appearance.icon("search")}</button><input type="search" name="q" maxlength="120" value="{e(value)}"'
            f' placeholder="{e(label)}" aria-label="{e(label)}"></div>')


def requests(request, db):
    q, category = request.query("q").strip()[:120], request.query("category")
    category = category if category in appl.CATEGORIES else ""
    user = request.user
    catalog = bool(q or category or request.query("mode") == "catalog")
    catalog_url = "/requests?mode=catalog"
    first = ' aria-current="page"' if catalog else ""
    second = '' if catalog else ' aria-current="page"'
    tabs = (f'<nav class="switcher dean-directory-tabs" aria-label="Разделы заявок">'
            f'<a href="{catalog_url}"{first}>Подать заявку</a>'
            f'<a href="/requests"{second}>Мои заявки</a></nav>')
    if catalog:
        filters = hidden("mode", "catalog") + hidden("category", category)
        filters += directory_search_field("Найти услугу", q)
        selector = f'<form method="get" action="/requests" class="dean-directory-search">{filters}</form>'
        services = appl.catalog(db, user, q=q, category=category)
        if q or category or request.query("view") == "all":
            selector += back(catalog_url, "Ко всем категориям")
            heading = appl.CATEGORIES.get(category, "Результаты поиска" if q else "Все услуги")
            content = (f'<section class="dean-request-services" aria-labelledby="request-services-title">'
                       f'<h2 id="request-services-title">{e(heading)}</h2>{services_items(services)}</section>')
        else:
            groups = []
            for key in ("documents", "study", "campus", "support", "practice"):
                label = appl.CATEGORIES[key]
                total = sum(row["category"] == key for row in services)
                if total:
                    glyph = {"study": "book", "documents": "file", "support": "help",
                             "campus": "building", "practice": "calendar"}[key]
                    groups.append(appearance.service_tile(catalog_url + "&category=" + key, label,
                                  appearance.count_label(total, "услуга", "услуги", "услуг"), glyph))
            if groups:
                groups.append(appearance.service_tile(catalog_url + "&view=all", "Все услуги", "", "services"))
                content = (f'<section><h2 class="dean-request-category-title">Категории</h2>'
                           f'<div class="rw-service-tiles dean-directory-grid">{"".join(groups)}</div></section>')
                common = ("enrolment-certificate", "academic-transcript")
                available = {row["id"]: row for row in services if row["id"] in common}
                popular = "".join(appearance.priority_link("/services/" + key, available[key]["name"],
                                  appl.CATEGORIES[available[key]["category"]], "file") for key in common if key in available)
                if popular:
                    content += f'<section class="dean-request-popular"><h2>Часто заказывают</h2>{popular}</section>'
                content = f'<div class="dean-request-catalog">{content}</div>'
            else:
                content = empty("Для этой группы пока нет доступных услуг.")
        body = (f'<div class="dean-request-layout">{tabs}<div class="dean-request-controls">{selector}</div>'
                f'<div class="dean-request-results">{content}</div></div>')
        return page(request, "Заявки", body, section="requests")
    selected = request.query("status")
    selected = selected if selected in appl.STATUSES else ""
    filters = select_field("status", "Статус", [("", "Все статусы"), *appl.STATUSES.items()], selected)
    selector = f'<form method="get" action="/requests" class="inline">{filters}{button("Показать")}</form>'
    rows = appl.applications(db, user, status=selected)
    request_list = request_items(rows, user["timezone"], status_filter=selected)
    if not rows and not selected:
        request_list = (f'<div class="dean-friendly-empty"><img src="{asset_url("assistant-mascot.svg")}" alt="" width="52" height="52">'
                        f'<strong>Заявок пока нет</strong><p>Первую можно подать из каталога.</p>'
                        f'{link(catalog_url, "Выбрать услугу")}</div>')
    mine = panel("Статусы заявок", selector + request_list,
                 link("/documents", "Документы"), key="my-requests")
    return page(request, "Заявки", tabs + mine, section="requests")


def application_inputs(request, db, schema, values):
    fields = []
    for spec in schema["fields"]:
        key, label, kind = spec["key"], spec["label"], spec["kind"]
        value = values.get(key, spec.get("default", ""))
        if kind == "reference":
            choices = application_forms.options(db, request.user, spec["source"])
            if value and value not in choices:
                snapshot = values.get("_links", {}).get(key, {})
                choices[value] = snapshot.get("title", "Ранее выбранная запись") + " (недоступно)"
            fields.append(select_field(key, label, [("", "Не выбрано"), *choices.items()], value))
        elif kind == "select":
            choices = list(spec["choices"].items())
            if not any(option == "" for option, _label in choices) and not spec["default"]:
                choices.insert(0, ("", "Выбери значение"))
            control = select_field(key, label, choices, value)
            if key in {"copies", "pickup"}:
                control = f'<div data-paper-field>{control}</div>'
            if key == "delivery":
                control += ('<p class="meta">Электронный документ появится в «Документах» как PDF. '
                            'Для бумажного экземпляра выбери место получения.</p>')
            fields.append(control)
        elif key == "purpose":
            fields.append(f'<label>{e(label)} <span class="meta">Обязательно при отправке</span>'
                          f'<input name="purpose" value="{e(value)}" aria-describedby="purpose-help" '
                          f'minlength="{spec["minimum"]}" maxlength="{spec["limit"]}" required>'
                          f'<span id="purpose-help" class="meta">Например, «Для подтверждения обучения». '
                          f'От {spec["minimum"]} до {spec["limit"]} символов.</span></label>')
        elif kind == "textarea":
            fields.append(textarea(key, label, spec["limit"], value=value))
        else:
            options = f'maxlength="{spec["limit"]}"'
            if kind == "integer":
                options += ' inputmode="numeric"'
            fields.append(input_field(key, label, value, options, "date" if kind == "date" else "text"))
    return "".join(fields)


def request_form(request, db, service_id, message="", values=None):
    row = appl.catalog(db, request.user, service_id)
    schema = application_forms.definition(row)
    owner = svc.profile(db, request.user)
    feedback = f'<p class="error" role="alert" tabindex="-1" data-restore-focus>{e(message)}</p>' if message else ""
    selection = {}
    for spec in schema["fields"]:
        if spec["kind"] == "reference":
            candidate = request.query(spec["key"])
            if candidate in application_forms.options(db, request.user, spec["source"]):
                selection[spec["key"]] = candidate
    fields = application_inputs(request, db, schema, values if values is not None else selection)
    if row["form_json"]:
        fields += hidden("form_version",schema["version"])
    if not schema["required_file"]:
        fields += button("Отправить заявку")
    fields += f'<button class="quiet" formaction="/services/{e(service_id)}/draft" formnovalidate>Сохранить черновик</button>'
    fields += ('<p class="meta">Сохрани черновик и приложи подтверждающий файл. '
               'Отправь заявку из карточки черновика.</p>' if schema["required_file"] else
               '<p class="meta">В черновике можно сохранить данные, приложить файл и отправить заявку позже.</p>')
    applicant = f'<p class="meta">Заявитель: {e(owner["full_name"])} · {e(owner["group_name"])}</p>'
    days = appearance.count_label(row["target_days"], "календарный день", "календарных дня", "календарных дней")
    term = f'<p class="ux-service-term"><strong>Срок в демо</strong> · {days} с отправки</p>'
    body = form(request, f"/services/{service_id}", fields, command=True, recover=True)
    return page(request, row["name"], f'<section class="panel rw-form-panel">{applicant}{feedback}{body}</section>'
                + back("/requests?mode=catalog", "К каталогу услуг"), row["description"], section="request-form",
                heading_action=term)


def document_links(did, *, application_id=""):
    href = f"/documents/{did}"
    extra = (link(href + "/share", "Поделиться", "dean-secondary-action")
             + link(f"/requests/{application_id}", "К заявке", "dean-secondary-action")) if application_id else ""
    return (f'<div class="dean-document-actions">{link(href + "?download=1", "Скачать PDF", "rw-primary-link")}'
            f'{link(href + "?preview=1", "Открыть PDF", "dean-secondary-action")}'
            f'{extra}</div>')


def document_preview(request, doc):
    href = f'/documents/{doc["id"]}'
    rows = document_text(doc["pdf"])
    content = ("".join(f"<p>{e(line)}</p>" for line in rows if line)
               if rows else '<p>Для просмотра этого документа скачай PDF.</p>')
    actions = (f'<div class="dean-document-actions">'
               f'{link(href + "?download=1", "Скачать PDF", "rw-primary-link")}'
               f'{link(href + "#view=FitH", "Открыть исходный PDF", "dean-secondary-action")}</div>')
    return page(request, doc["title"], back(f'/requests/{doc["application_id"]}', "К заявке")
                + actions + panel("Содержимое документа", content, style="dean-document-text"),
                "Демонстрационный документ · без юридической силы", section="documents")


def student_application_actions(request, db, row, values):
    aid, status = row["id"], row["status"]
    version = hidden("version", row["version"])
    body = ""
    if status in appl.EDITABLE:
        fields = (application_inputs(request, db, application_forms.definition(row), values) + version
                  + '<button formnovalidate>Сохранить</button>')
        label = "Отправить повторно" if status == "needs_changes" else "Отправить заявку"
        fields += f'<button formaction="/requests/{aid}/send">{label}</button>'
        body += panel("Данные заявления", form(request, f"/requests/{aid}/save", fields, command=True, recover=True),
                      key="request-data")
    if status in appl.CANCELLABLE:
        body += panel("Отмена", '<p>После отмены сотрудник прекратит работу. История останется в карточке.</p>'
                      + form(request, f"/requests/{aid}/cancel", version + button("Отменить заявку"), command=True))
    if status in appl.ACTIVE:
        body += panel("Написать сотруднику", form(request, f"/requests/{aid}/comment",
                      textarea("note", "Сообщение", 500, required=True) + button("Отправить"), command=True))
    return body


def staff_application_actions(request, db, row):
    aid, version = row["id"], hidden("version", row["version"])
    parts = []
    guidance = {
        "review": ("Начало проверки", "Сообщение студенту", "Добавь сообщение, если нужно уточнить детали."),
        "ready": ("Выдача документа", "Пояснение к документу", "Можно уточнить, где получить документ."),
        "completed": ("Результат проверки", "Решение по заявлению", "Кратко опиши результат для студента."),
        "needs_changes": ("Возврат на исправление", "Что исправить", "Перечисли данные или документы, которые нужно обновить."),
        "refused": ("Отказ", "Причина отказа", "Укажи основание, чтобы студент понял решение."),
    }
    for index, (status, label) in enumerate(appl.staff_actions(row)):
        path = f"/staff/requests/{aid}" if not index else f"/staff/requests/{aid}/{status}"
        needs_reason = status in {"refused", "needs_changes"} or (
            status == "completed" and bool(application_forms.definition(row)["result"]))
        title, field_label, hint = guidance[status]
        fields = (f'<h3 class="rw-staff-action-title">{e(title)}</h3>'
                  f'<p class="rw-staff-action-hint">{e(hint)}</p>'
                  + hidden("status", status) + version + textarea("note", field_label, 500, required=needs_reason))
        style = "rw-action-danger" if status == "refused" else "rw-action-attention" if status == "needs_changes" else ""
        parts.append(form(request, path, fields + button(label), command=True, style=style))
    if row["status"] in appl.ACTIVE:
        choices = [(x["id"], x["full_name"]) for x in appl.staff_candidates(db, row)]
        fields = version + select_field("assignee", "Ответственный", choices, row["assignee_id"])
        parts.append(form(request, f"/staff/requests/{aid}/assign", fields + button("Назначить"), command=True))
        fields = ('<h3 class="rw-staff-action-title">Переписка</h3>'
                  '<p class="rw-staff-action-hint">Студент увидит сообщение в истории заявления.</p>'
                  + version + textarea("note", "Сообщение в истории", 500, required=True))
        parts.append(form(request, f"/staff/requests/{aid}/comment", fields + button("Добавить комментарий"), command=True))
    return panel("Действия сотрудника", '<div class="grid rw-staff-actions">' + "".join(parts) + "</div>",
                 key="request-actions") if parts else ""


def answer_summary(schema, values):
    lines = []
    for spec in schema["fields"]:
        key = spec["key"]
        value = values.get(key)
        if not value:
            continue
        if spec["kind"] == "reference":
            snapshot = values.get("_links", {}).get(key, {})
            value = snapshot.get("title", value)
            if snapshot.get("verified_at"):
                value += " · подтверждено сотрудником на момент подачи"
        elif spec["kind"] == "select":
            value = spec["choices"].get(value, value)
        lines.append(f'<p class="multiline"><strong>{e(spec["label"])}.</strong> {e(value)}</p>')
    if values.get("_plan"):
        for block in values["_plan"]:
            titles = ", ".join(f'{row["title"]} ({row["credits"]} кр.)' for row in block["items"])
            lines.append(f'<p><strong>{e(block["block"])}.</strong> {e(titles or "Выбор пуст")}</p>')
    return "".join(lines)


def attachment_list(request, row, rows, *, editable=False):
    body = []
    for item in rows:
        controls = ""
        if editable:
            controls = form(request, f'/requests/{row["id"]}/attachments/{item["id"]}/remove',
                            hidden("version", row["version"]) + button("Убрать из черновика"), command=True)
        body.append(f'<li><div>{link("/attachments/" + item["id"], item["name"])}'
                    f'<p class="meta">{item["size"]} байт</p></div>{controls}</li>')
    return f'<ul class="rows results">{"".join(body)}</ul>' if body else empty("Вложений нет.")


def application_files(request, db, row, history):
    editable = request.user["role"] == "student" and row["status"] in appl.EDITABLE
    latest = history[-1]["id"] if history and not editable else None
    rows = attachment.files(db, row["id"], latest) if editable or latest else []
    body = attachment_list(request, row, rows, editable=editable)
    if editable:
        fields = hidden("version", row["version"]) + input_field(
            "file", "Файл", options='accept=".png,.txt" required', kind="file")
        body += '<p class="meta">PNG или TXT UTF-8, до 2 МиБ каждый. До трёх файлов в подаче.</p>'
        body += form(request, f'/requests/{row["id"]}/attachments', fields + button("Приложить файл"),
                     command=True, multipart=True)
    return panel("Вложения", body)


def request_detail(request, db, aid):
    with read_snapshot(db):
        return request_detail_snapshot(request, db, aid)


def request_detail_snapshot(request, db, aid):
    user = request.user
    row, changes = appl.application_details(db, user, aid)
    staff = user["role"] == "staff"
    status = row["status"]
    body = f'<p><strong>Статус</strong> {application_badge(status)}</p>'
    if status in {"needs_changes", "refused"}:
        last = next((change for change in reversed(changes) if change["status"] == status
                     and change["event_kind"] == "status"), None)
        returned = status == "needs_changes"
        title = "Что нужно исправить" if returned else "Причина отказа"
        remark = last["note"] if last and last["note"] else "Уточни детали у сотрудника в истории заявления."
        action = link("#request-data", "Исправить заявление") if returned and not staff else ""
        body += f'<div class="rw-request-reason"><strong>{title}</strong><p>{e(remark)}</p>{action}</div>'
    if not staff and status != "needs_changes":
        next_step = {
            "draft": "Заполни данные заявки и отправь её сотруднику.",
            "submitted": "Сотрудник возьмёт заявку в работу. Обновление появится в уведомлениях.",
            "review": "Сотрудник проверяет заявку. Ответ появится в уведомлениях.",
            "needs_changes": "Исправь данные по замечанию и отправь заявку повторно.",
            "resubmitted": "Сотрудник проверит исправления. Обновление появится в уведомлениях.",
            "completed": "Результат проверки указан в карточке.",
            "ready": ("Открой или скачай PDF. После получения нажми «Отметить полученным»." if row["document_id"]
                      else "Забери бумажный документ в указанном ниже месте и подтверди получение здесь."),
            "received": ("Получение подтверждено. PDF доступен в «Документах»." if row["document_id"]
                         else "Получение бумажного документа подтверждено."),
            "refused": "При необходимости подай новое заявление.",
            "cancelled": "Работа по заявке завершена. При необходимости подай новую.",
        }[status]
        body += f'<p><strong>Дальше</strong> {e(next_step)}</p>'
        if status == "draft":
            body += back("#request-data", "Продолжить оформление")
    if staff and appl.staff_actions(row):
        body += '<p class="rw-only rw-jump">' + link("#request-actions", "К действиям по заявке") + "</p>"
    body += f'<p>{e(row["student_name"])} · {e(row["group_name"])}</p>'
    if row["assignee_name"]:
        body += f'<p class="meta">Ответственный: {e(row["assignee_name"])}</p>'
    if row["due_at"] and row["status"] in appl.ACTIVE:
        body += f'<p class="meta">Демо-срок: {e(local_label(row["due_at"], user["timezone"]))}</p>'
    submissions = appl.submissions(db, aid)
    values = appl.visible_answers(db, user, row, submissions)
    body += answer_summary(application_forms.definition(row), values)
    if row["resolution"]:
        body += f'<p class="change">{e(row["resolution"])}</p>'
    if row["service_id"] in finances.SUPPORT:
        award = db.execute("SELECT id FROM finance_award WHERE source_app=?", (aid,)).fetchone()
        if award:
            url = "/staff/finance/" if staff else "/finance/awards/"
            body += f'<p>{link(url + award["id"], "К назначению")}</p>'
    if row["service_id"] == "practice-enrolment":
        case = db.execute("""SELECT id,supervisor_id FROM practice_case WHERE application_id=? AND university_id=?""",
                          (aid, user["university_id"])).fetchone()
        if case and (not staff or case["supervisor_id"] == user["id"]):
            url = "/staff/practice/" if staff else "/practice/"
            body += f'<p>{link(url + case["id"], "К практике")}</p>'
    if row["document_id"]:
        body += f'<div id="request-document">{document_links(row["document_id"])}</div>'
    if not staff and status == "ready" and (row["document_id"] or values.get("delivery") == "paper"):
        body += '<p class="meta">Подтверди, когда документ получен. Скачивание само по себе не меняет статус.</p>'
        body += form(request, f"/requests/{aid}/receive", hidden("version", row["version"])
                     + button("Отметить полученным"), command=True)
    labels = {"saved": "Данные сохранены", "assignment": "Назначен ответственный", "comment": "Комментарий"}
    history = []
    for change in changes:
        note = f'<p class="multiline">{e(change["note"])}</p>' if change["note"] else ""
        label = labels.get(change["event_kind"], appl.STATUSES[change["status"]])
        meta = f'{local_label(change["created_at"], user["timezone"])} · {change["actor"]}'
        history.append(f'<li><strong>{e(label)}</strong><p class="meta">{e(meta)}</p>{note}</li>')
    heading = f'<article class="panel">{body}</article>'
    if not staff:
        heading += student_application_actions(request, db, row, values)
    heading += application_files(request, db, row, submissions)
    heading += panel("История", f'<ol class="timeline">{"".join(history)}</ol>', key="request-history")
    if submissions:
        versions = []
        for number, item in enumerate(submissions, 1):
            schema = json.loads(item["form_json"])
            label = f'Подача {number} · {local_label(item["submitted_at"], user["timezone"])}'
            versions.append(f'<details><summary>{e(label)}</summary>'
                            + answer_summary(schema, json.loads(item["answers_json"]))
                            + attachment_list(request, row, attachment.files(db, aid, item["id"])) + '</details>')
        heading += panel("Версии подачи", "".join(versions))
    if staff:
        heading += staff_application_actions(request, db, row)
    selected = request.query("status")
    target = "/staff" if staff else "/requests"
    if not staff and selected in appl.STATUSES:
        target += "?" + urlencode({"status": selected})
    heading += back(target, "В очередь" if staff else "К заявкам")
    return page(request, row["service_name"], heading, f'Заявка № {aid[:8]}', section="request-detail")


def document_archive(request, db):
    rows = appl.documents(db, request.user)
    items = "".join(f'<li><div><strong>{e(row["title"])}</strong>'
                    f'<p class="meta">{e(local_label(row["issued_at"], request.user["timezone"]))} · {e(appl.STATUSES[row["status"]])}</p>'
                    f'{document_links(row["id"], application_id=row["application_id"])}</div></li>' for row in rows)
    content = f'<ul class="rows">{items}</ul>' if items else (
        empty("Здесь появятся готовые электронные документы по твоим заявкам.")
        + link("/requests?mode=catalog&category=documents", "Заказать документ", "rw-primary-link"))
    return page(request, "Документы", panel("Мои документы", content) + back("/requests", "К заявкам"),
                section="documents")


NOTICE_LINKS = {
    "support": ("/support/", "Открыть обращение"),
    "application": ("/requests/", "К заявке"), "schedule": ("/schedule/", "К занятию"),
    "announcement": ("/announcements/", "К объявлению"), "exam": ("/grades/", "К экзамену"),
    "grade": ("/grades/", "К результату"), "task": ("/tasks/", "К задаче"),
    "event": ("/events/", "К событию"), "booking": ("/bookings/", "К записи"),
    "activity": ("/slots/", "К регистрации"), "library": ("/library/", "К выдаче"),
    "retake": ("/retakes/", "К пересдаче"),
    "award": ("/finance/awards/", "К назначению"), "charge": ("/finance/charges/", "К начислению"),
    "budget": ("/finance/budget", "К бюджету"),
    "equipment": ("/equipment/loans/", "К выдаче"), "library_hold": ("/library/holds/", "К резерву"),
    "team": ("/teams/", "К команде"), "team_invite": ("/teams/invitations/", "К приглашению"),
    "meeting": ("/teams/meetings/", "К встрече"), "shared_note": ("/teams/", "К заметке"),
    "practice": ("/practice/", "К практике"), "portfolio": ("/portfolio/", "К достижению"),
    "course": ("/courses/", "К курсу"), "work": ("/work/", "К работе"),
    "work_result": ("/work/", "К результату"),
    "public_reminder": ("/schedule/public/", "К занятию"),
    "public_change": ("/schedule/public/", "К занятию"),
}
NOTICE_TYPES = {"support": "Поддержка", "application": "Заявка", "schedule": "Расписание", "announcement": "Объявление",
                "exam": "Срок экзамена", "grade": "Успеваемость", "task": "Срок задачи", "event": "Личное событие",
                "booking": "Бронирование", "activity": "Лист ожидания", "library": "Библиотека"}
NOTICE_TYPES.update(retake="Пересдача", award="Назначение", charge="Начисление", budget="Личный бюджет",
                    equipment="Техника", library_hold="Резерв книги", team="Команда", team_invite="Приглашение",
                    meeting="Совместная встреча", shared_note="Общая заметка", practice="Практика",
                    portfolio="Достижение", course="Курс", work="Учебная работа",
                    work_result="Результат работы", public_reminder="Публичное расписание",
                    public_change="Изменение публичной пары")
NOTICE_FILTER_TYPES = {**NOTICE_TYPES, "announcement": "Объявления", "deadlines": "Дедлайны",
                       "changes": "Изменения расписания"}


NOTICE_PAGE_SIZE = 12


def notice_page(raw):
    return max(1, int(raw)) if len(raw) <= 6 and raw.isascii() and raw.isdecimal() else 1


def notices_url(unread=False, kind="", focus="", *, number=1):
    params = {}
    if unread:
        params["filter"] = "unread"
    if kind in NOTICE_FILTER_TYPES:
        params["kind"] = kind
    if focus:
        params["focus"] = focus
    if number > 1:
        params["page"] = number
    return "/notifications" + ("?" + urlencode(params) if params else "")


def notice_filters(unread, kind, focus="", *, number=1):
    fields = hidden("filter", "unread" if unread else "") + hidden("kind", kind) + hidden("focus", focus)
    return fields + (hidden("page", number) if number > 1 else "")


def notice_items(request, db, rows, compact=False, *, number=1):
    focus = request.query("focus") if not compact else ""
    if not rows:
        attrs = ' tabindex="-1" data-restore-focus' if focus else ""
        return f'<p class="empty"{attrs}>Здесь пока нет уведомлений.</p>'
    items = []
    hide_money = finances.amounts_hidden(db, request.user)
    hide_private = profile_tools.hidden_preview(db, request.user)
    statuses = {}
    if not hide_private and any(row["kind"] == "application" for row in rows):
        statuses = {row["id"]: row["status"] for row in appl.applications(db, request.user)}
    unread_only, kind = request.query("filter") == "unread", request.query("kind")
    for index, row in enumerate(rows):
        href, caption = NOTICE_LINKS.get(row["kind"], ("", ""))
        target = href + row["target_id"] if href else ""
        if row["kind"] == "event":
            when = row["event_key"].split(":", 2)[-1]
            day = local_value(when, request.user["timezone"]).date()
            target += "?" + urlencode({"occurrence": day.isoformat()})
        elif row["kind"] == "shared_note":
            team_id = row["event_key"].split(":")[1]
            target = f"/teams/{team_id}/notes/{row['target_id']}"
        unread = not row["read_at"]
        read_action = ""
        if unread and not compact:
            next_focus = row["id"]
            if unread_only:
                next_focus = (rows[index + 1]["id"] if index + 1 < len(rows) else
                              rows[index - 1]["id"] if index else "list")
            fields = notice_filters(unread_only, kind, next_focus, number=number)
            read_action = form(request, f'/notifications/{row["id"]}/read',
                               fields + '<button class="quiet">Отметить прочитанным</button>')
        meta = f'{NOTICE_TYPES.get(row["kind"], "Обновление")} · {local_label(row["created_at"], request.user["timezone"])}'
        message = row["body"]
        if not compact and row["kind"] == "announcement" and row["announcement_body"]:
            news = " ".join(row["announcement_body"].split())
            message = news if len(news) <= 220 else news[:220].rstrip() + "…"
        if not hide_money and not hide_private and row["kind"] in {"award", "charge"}:
            table = "finance_award" if row["kind"] == "award" else "tuition_charge"
            amount = db.execute(f"SELECT amount FROM {table} WHERE university_id=? AND student_id=? AND id=?",
                                (*workspace.scope(request.user), row["target_id"])).fetchone()
            if amount:
                message += " · " + finances.rubles(amount[0])
        title = row["title"]
        latest = ""
        if row["kind"] == "application" and not hide_private:
            aid = row["target_id"]
            caption += f" № {aid[:8]}"
            current = statuses.get(aid)
            action = row["event_key"].split(":")[2:3]
            event_status = action[0] if action else ""
            if event_status == "sent":
                event_status = "resubmitted" if "повторно" in title.lower() else "submitted"
            if current and event_status in appl.STATUSES and event_status != current:
                title = "Ранее · " + title
                latest = f'<p class="ux-notice-current">Сейчас: {e(appl.STATUSES[current])}</p>'
                target += "#request-history"
        if hide_private and row["kind"] in profile_tools.SENSITIVE:
            title, message = "Личное обновление", "Детали доступны в карточке."
        attrs = ' tabindex="-1" data-restore-focus' if focus == row["id"] else ""
        destination_link = link(target, caption) if target else ""
        body = (f'<li data-unread="{"true" if unread else "false"}" data-kind="{e(row["kind"])}"{attrs}>'
                f'<div><p class="meta">{e(meta)}{" · Не прочитано" if unread else ""}</p>'
                f'<strong>{e(title)}</strong><p>{e(message)}</p>{latest}'
                f'{destination_link}</div>{read_action}</li>')
        items.append(body)
    restore = ' tabindex="-1" data-restore-focus' if focus and not any(row["id"] == focus for row in rows) else ""
    return f'<ul class="rows notices"{restore}>{"".join(items)}</ul>'


def notifications(request, db):
    unread = request.query("filter") == "unread"
    kind = request.query("kind")
    kind = kind if kind in NOTICE_FILTER_TYPES else ""

    def tab(url, label, active):
        current = ' aria-current="page"' if active else ''
        return f'<a href="{e(url)}"{current}>{e(label)}</a>'

    tabs = tab(notices_url(kind=kind), "Все", not unread) + tab(notices_url(True, kind), "Непрочитанные", unread)
    filters = ("announcement", "deadlines", "changes")
    quick = tab(notices_url(unread), "Все темы", not kind) + "".join(
        tab(notices_url(unread, code), NOTICE_FILTER_TYPES[code], kind == code) for code in filters
    )
    types = [(code, label) for code, label in NOTICE_FILTER_TYPES.items()
             if (not code.startswith("public_") or request.public)
             and (code != "schedule" or not request.public)]
    filter_type = select_field("kind", "Тип уведомления", [("", "Все типы"), *types], kind)
    if unread:
        filter_type = hidden("filter", "unread") + filter_type
    content = f'<nav class="switcher" aria-label="Вид уведомлений">{tabs}</nav>'
    content += f'<nav class="dean-notice-categories" aria-label="Темы уведомлений">{quick}</nav>'
    content += ('<details class="dean-notice-filter"><summary>Все типы уведомлений</summary>'
                f'<form method="get" action="/notifications" class="inline">{filter_type}{button("Показать")}</form></details>')
    number = notice_page(request.query("page"))
    rows = svc.notifications(db, request.user, unread, NOTICE_PAGE_SIZE + 1, kind, request.public,
                             offset=(number - 1) * NOTICE_PAGE_SIZE)
    if not rows and number > 2 and request.query("focus"):
        number -= 1
        rows = svc.notifications(db, request.user, unread, NOTICE_PAGE_SIZE + 1, kind, request.public,
                                 offset=(number - 1) * NOTICE_PAGE_SIZE)
    if not rows and number > 1:
        number = 1
        rows = svc.notifications(db, request.user, unread, NOTICE_PAGE_SIZE + 1, kind, request.public)
    has_next = len(rows) > NOTICE_PAGE_SIZE
    rows = rows[:NOTICE_PAGE_SIZE]
    items = notice_items(request, db, rows, number=number)
    if number > 1 or has_next:
        pager = ""
        for available, target, relation, label in ((number > 1, number - 1, "prev", "Назад"),
                                                   (has_next, number + 1, "next", "Дальше")):
            if available:
                href = notices_url(unread, kind, number=target) + "#notification-feed"
                pager += f'<a href="{e(href)}" rel="{relation}">{label}</a>'
        items += (f'<nav class="dean-notice-pages" aria-label="Страницы уведомлений">'
                  f'<span>Страница {number}</span>{pager}</nav>')
    has_unread = any(not row["read_at"] for row in rows)
    if not unread and not has_unread and rows:
        has_unread = bool(svc.notifications(db, request.user, True, 1, kind, request.public))
    read = (form(request, "/notifications/read-all", notice_filters(unread, kind, "list")
                 + '<button class="quiet">Отметить все прочитанными</button>')
            if has_unread else "")
    title = NOTICE_FILTER_TYPES.get(kind, "Мои уведомления")
    content += panel(title, items, read, key="notification-feed")
    chosen = reminders.settings(db, request.user)
    controls = ""
    for key, label in reminders.OPTIONAL.items():
        checked = " checked" if chosen[key] else ""
        controls += f'<label class="check"><input type="checkbox" name="{key}" value="1"{checked}>{e(label)}</label>'
    settings = form(request, "/notifications/settings", controls + button("Сохранить настройки"))
    content += f'<details class="panel"><summary>Настроить напоминания</summary>{settings}</details>'
    return page(request, "Уведомления", content, "Объявления деканата, дедлайны и изменения расписания.", section="notifications")


def search_page(request, db):
    from . import demo_views

    user = request.user
    workspace.scope(user)
    q, kind = request.query("q")[:120], request.query("kind")
    query = input_field("q", "Запрос", q, 'maxlength="120"', "search")
    types = search.PUBLIC_TYPES if request.public else search.TYPES
    query += select_field("kind", "Искать в", [("", "Все разделы"), *types.items()], kind)
    form_html = f'<form method="get" action="/search" class="inline">{query}{button("Найти")}</form>'
    found = search.matches(db, user, q, kind, request.public)
    words = q.casefold().split()
    if words and kind in {"", "service"} and demo_views.campus_pass_available(request):
        caption = "Учебный пропуск · QR-код"
        if all(word in ("демопропуск " + caption).casefold() for word in words):
            found.insert(0, search.Match("service", "Демопропуск", "/campus-pass", caption))
    items = ""
    for item in found[:60]:
        caption = " · ".join(filter(None, (types[item.kind], item.summary[:140])))
        items += f'<li><div>{link(item.href, item.title)}<p class="meta">{e(caption)}</p></div></li>'
    if q.strip():
        body = f'<ul class="rows">{items}</ul>' if items else empty("Ничего не найдено.")
        title = f"Найдено: {len(found)}"
    else:
        body = empty("Введи название или часть описания.")
        title = "Результаты"
    return page(request, "Поиск", form_html + panel(title, body))


def announcement(request, db, aid):
    row = svc.announcement(db, request.user, aid)
    staff = request.user["role"] == "staff"
    target = "/staff/announcements" if staff else "/notifications?kind=announcement"
    return page(request, row["title"], f'<article class="panel multiline">{e(row["body"])}</article>'
                + back(target, "К объявлениям"),
                local_label(row["created_at"], request.user["timezone"]))


def announcement_form(request, groups):
    fields = select_field("group", "Группа", [(row["id"], row["name"]) for row in groups])
    fields += input_field("title", "Заголовок", options='maxlength="100" required')
    fields += textarea("body", "Текст", 1500, required=True) + button("Опубликовать")
    return form(request, "/staff/announcements", fields, command=True)


def staff_announcements(request, db):
    groups = svc.staff_groups(db, request.user)
    editor = (announcement_form(request, groups) if groups
              else empty("Публикация доступна после назначения учебной группы."))
    notices = svc.staff_announcements(db, request.user)
    rows = "".join(
        f'<li><div>{link("/announcements/" + row["id"], row["title"])}'
        f'<p class="meta">{e(row["group_name"])} · {e(local_label(row["created_at"], request.user["timezone"]))}'
        f' · Получателей: {row["recipients"]}</p></div></li>'
        for row in notices
    )
    history = f'<ul class="rows">{rows}</ul>' if rows else empty("Опубликованных объявлений пока нет.")
    body = (panel("Новое объявление", editor, key="staff-announcement-compose")
            + panel("Опубликованные объявления", history, key="staff-announcement-history"))
    return page(request, "Объявления", body, "Студенты выбранной группы увидят объявление в своих уведомлениях.",
                section="staff-announcements")


def staff_schedule_url(day, status="", service=""):
    params = {"date": day.isoformat()}
    if status:
        params["status"] = status
    if service:
        params["service"] = service
    return "/staff?" + urlencode(params) + "#staff-schedule"


def staff_schedule(db, user, focus, today, groups, status, service):
    first = focus - timedelta(days=focus.weekday())
    last = first + timedelta(days=6)
    previous = staff_schedule_url(first - timedelta(days=7), status, service)
    following = staff_schedule_url(first + timedelta(days=7), status, service)
    controls = ('<nav class="toolbar" aria-label="Недели расписания сотрудника">'
                + link(previous, "← Предыдущая неделя", "dean-secondary-action")
                + link(staff_schedule_url(today, status, service), "Текущая неделя", "dean-secondary-action")
                + link(following, "Следующая неделя →", "dean-secondary-action") + '</nav>')
    date_picker = (f'<form method="get" action="/staff#staff-schedule" class="inline">'
                   f'{hidden("status", status)}{hidden("service", service)}'
                   f'{input_field("date", "Дата в нужной неделе", focus.isoformat(), "required", "date")}'
                   f'{button("Открыть неделю")}</form>')
    names = {row["id"]: row["name"] for row in groups}
    zone = user["timezone"]
    days = {}
    for row in staff_lessons(db, user, week=focus):
        start, end = (local_value(row[key], zone) for key in ("starts_at", "ends_at"))
        stop = end.strftime("%H:%M" if end.date() == start.date() else "%d.%m %H:%M")
        times = timeline_time(row["starts_at"], f"{start:%H:%M}–{stop}")
        room = (row["room"] or "Аудитория не указана").replace("-й этаж", "‑й этаж")
        days.setdefault(start.date(), []).append(
            f'<li>{times}<div>{link(f"/staff/schedule/{row["id"]}", row["subject"])}'
            f'<p class="meta">{e(names[row["group_id"]])} · {e(room)}</p></div></li>'
        )
    items = "".join(f'<h3>{WEEKDAYS[day.weekday()]}, {day_label(day)}</h3>'
                    f'<ul class="rows lessons">{"".join(rows)}</ul>' for day, rows in days.items())
    period = (f"{schedule_display.period_label(first, last)} {first.year}"
              if first.year == last.year else f"{day_label(first)} {first.year} — {day_label(last)} {last.year}")
    heading = f"Неделя · {period}"
    return panel(heading, controls + date_picker + (items or empty("На этой неделе занятий нет.")))


def staff(request, db):
    from . import room_occupancy

    groups = svc.staff_groups(db, request.user)
    today = today_for(request.user)
    focus = get_date(request.query("date"), today)
    selected = request.query("status", "")
    options = '<option value="">Все статусы</option>' + "".join(
        f'<option value="{key}"{" selected" if key == selected else ""}>{label}</option>'
        for key, label in appl.STATUSES.items()
    )
    service_choices = [("", "Все услуги"), *((row["id"], row["name"]) for row in appl.catalog(db, request.user))]
    selected_service = request.query("service")
    service_field = select_field("service", "Услуга", service_choices, selected_service)
    selector = (f'<form method="get" action="/staff" class="inline">{hidden("date", focus.isoformat())}'
                f'<label>Статус<select name="status">{options}</select></label>{service_field}{button("Показать")}</form>')
    queue = request_items(appl.applications(db, request.user, status=selected, service_id=selected_service),
                          request.user["timezone"], staff=True)
    body = f'<p>{link("/staff/grades", "Учебные результаты")} · {link("/staff/retakes", "Пересдачи")} · {link("/staff/bookings", "Управление записью")} · {link("/staff/library", "Библиотечная выдача")}</p>'
    if room_occupancy.available(request.user):
        body += f'<p>{link(room_occupancy.PATH, room_occupancy.LABEL)}</p>'
    body += f'<p>{link("/staff/finance", "Назначения и демо-выплаты")}</p>'
    body += f'<p>{link("/staff/moderation", "Жалобы на сообщения")}</p>'
    body += f'<p>{link("/staff/practice", "Практики на проверке")} · {link("/staff/achievements", "Подтверждение достижений")}</p>'
    request.railway_operator = public_sync.operator(db, request.user)
    if request.railway_operator:
        body += f'<p>{link("/staff/public", "Открытые источники")}</p>'
    apps, library, equipment, resources = campus.summary(db, request.user, timestamp())
    overview = panel("Сводка по доступным группам и ресурсам",
                  f'<p>Заявки в работе {apps["active"]} · просрочено {apps["overdue"]}</p>'
                  f'<p>Книг на руках {library["active"]} · просрочено {library["overdue"]}</p>'
                  f'<p>Единиц техники на руках {equipment["active"]} · просрочено {equipment["overdue"]}</p>'
                  f'<p>Ресурсы {resources["resources"]} · действующие записи {resources["bookings"]}</p>',
                  link("/staff/equipment", "Управление техникой"), style="rw-staff-overview")
    desk_queue = panel("Очередь заявок", selector + queue, key="staff-queue")
    body = (f'<div class="legacy-only">{body}</div>'
            f'<div class="rw-staff-grid">{desk_queue}{overview}</div>')
    body += '<div class="rw-staff-grid" id="staff-schedule">' + staff_schedule(
        db, request.user, focus, today, groups, selected, selected_service)
    if groups:
        body += panel("Объявление для группы", announcement_form(request, groups),
                      link("/staff/announcements", "История объявлений"))
    body += '</div><div class="toolbar legacy-only">' + link("/staff/support", "Поддержка") + " · " + link("/profile/sessions", "Сеансы") + "</div>"
    body += '<p class="legacy-only">' + link("/staff/work","Учебные работы") + '</p>'
    body += '<p class="legacy-only">' + link("/staff/manage","Услуги и ресурсы") + '</p>'
    sections_by_role = (("Учёба", (("/staff/grades", "Оценки"), ("/staff/retakes", "Пересдачи"), ("/staff/work", "Учебные работы"))),
              ("Кампус", (("/staff/manage", "Услуги и ресурсы"), ("/staff/bookings", "Управление записями"),
                          ("/staff/equipment", "Техника"), ("/staff/library", "Библиотечная выдача"))),
              ("Другие очереди", (("/staff/finance", "Назначения и демо-выплаты"), ("/staff/moderation", "Жалобы на сообщения"),
                                 ("/staff/practice", "Практики на проверке"), ("/staff/achievements", "Подтверждение достижений"),
                                 ("/staff/support", "Поддержка"))))
    sections = ''.join(panel(title, '<ul class="rows">' + ''.join(f'<li>{link(*item)}</li>' for item in links) + '</ul>')
                       for title, links in sections_by_role)
    if request.railway_operator:
        sections += panel("Публикация расписания", '<p>Сравнения, история и охват опубликованных недель.</p>',
                          link("/staff/public", "Открытые источники"))
    body += f'<div class="rw-only rw-service-groups">{sections}</div>'
    return page(request, "Кабинет сотрудника", ai_views.launcher() + body, request.user["full_name"], section="staff",
                heading_action=link("#staff-schedule", "К расписанию", "dean-secondary-action"),
                dashboard_chat=ai_views.chat(request, db, embedded=True))


def staff_lesson(request, row):
    from .activity_extensions import rooms
    from .lesson_route import banner

    zone = row["timezone"]
    start = local_value(row["starts_at"], zone).strftime("%Y-%m-%dT%H:%M")
    end = local_value(row["ends_at"], zone).strftime("%Y-%m-%dT%H:%M")
    fields = hidden("version", row["version"])
    fields += input_field("starts", "Начало", start, "required", "datetime-local")
    fields += input_field("ends", "Окончание", end, "required", "datetime-local")
    path = f'/staff/schedule/{row["id"]}'
    room = row["room"] or ""
    fields += input_field("room", "Аудитория", room, 'maxlength="80" required list="teaching-rooms"')
    fields += ('<datalist id="teaching-rooms">' + "".join(f'<option value="{e(label)}">'
               for label in rooms().values()) + '</datalist>')
    body = form(request, path, fields + button("Сохранить изменение"), command=True)
    body += f'<p>{link(f"/staff/attendance/{row["id"]}", "Отметки посещения")}</p>'
    day = local_value(row["starts_at"], request.user["timezone"]).date()
    return page(request, row["subject"], banner(room, path) + panel("Изменить занятие", body)
                + back(staff_schedule_url(day), "К неделе расписания"), f'Часовой пояс: {zone}')
