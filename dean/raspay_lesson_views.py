"""Lesson details and existing personal actions in the Raspay view."""

import json
from urllib.parse import urlencode

from . import calendar_tools, lesson_route, public_schedule, public_subjects, public_views, raspay, study_context, workspace
from .raspay_views import ROOT, duration, kind_badge, shell, subhead, time_label, url
from .schedule import local_value
from .ui import WEEKDAYS, button, day_label, e, form, hidden, input_field, link, textarea


def sources(request, event, published):
    facts = ""
    if published:
        rooms = json.loads(published["rooms_json"])
        room_links = []
        for label, path in rooms:
            href = public_views.room_href(path)
            room_links.append(link(href, label) if href else e(label))
        if room_links:
            facts += '<div><dt>Аудитория и корпус</dt><dd>' + " · ".join(room_links) + "</dd></div>"
        if not published["cancelled"]:
            facts += "<div><dt>Дисциплина</dt><dd>" + link(
                public_subjects.subject_href(request.public, event.subject), "Карточка дисциплины") + "</dd></div>"
        facts += '<div><dt>Источник</dt><dd>' + link(event.source_url, public_views.MODE[published["provenance"]]) + "</dd></div>"
    else:
        if event.room:
            facts += f'<div><dt>Аудитория и корпус</dt><dd>{e(event.room)}</dd></div>'
        if event.teacher:
            facts += f'<div><dt>Преподаватель</dt><dd>{e(event.teacher)}</dd></div>'
        if event.subject_id:
            search = "/search?" + urlencode({"q": event.subject, "kind": "subject"})
            facts += '<div><dt>Дисциплина</dt><dd>' + link(
                search, "Найти дисциплину") + "</dd></div>"
        facts += '<div><dt>Источник</dt><dd>Учебные демоданные</dd></div>'
    return '<h2 class="rp-section-title">О паре</h2><dl class="rp-facts">' + facts + "</dl>"


def notes(request, db, event, focus):
    path = ROOT + "/lesson/" + event.id + "/note"
    fields = hidden("date", focus.isoformat()) + hidden("context_revision", study_context.preference(db, request.user)[1])
    if request.public:
        saved = public_schedule.note(db, request.user, event.id)
        checked = ' checked' if saved and saved["remind"] else ""
        fields += textarea("body", "Моя заметка", 1600, value=saved["body"] if saved else "")
        fields += (f'<label class="rp-check"><input type="checkbox" name="remind" value="1"{checked}>'
                   'Напомнить за 30 минут</label>')
        existing = None
    else:
        existing = workspace.notes(db, request.user, "lesson", event.id)
        fields += textarea("body", "Новая заметка", 1600, required=True)
    items = "".join(f'<li><p>{e(row["body"])}</p>{link("/notes/" + row["id"], "Изменить")}</li>'
                    for row in (existing or ()))
    content = ("<ul>" + items + "</ul>") if items else ""
    details = form(request, path, fields + button("Сохранить заметку"), command=True)
    return f'<details class="rp-other"{" open" if request.query("note") == "1" else ""}><summary>Личные заметки</summary>{content}{details}</details>'


def detail(request, db, lesson_id, screen=None):
    user, selection = request.user, request.public
    event, published = raspay.lesson(db, user, selection, lesson_id)
    prefs = raspay.preference(db, user, selection)
    saved = calendar_tools.override(db, user, lesson_id) if published else None
    in_force = saved and saved["base_version"] == event.version and not published["cancelled"]
    starts, ends = (saved["starts_at"], saved["ends_at"]) if in_force else (event.starts_at, event.ends_at)
    start, end = local_value(starts, event.timezone), local_value(ends, event.timezone)
    day = start.date()
    back_day = study_context.date_value(request.query("date")) if request.query("date") else day
    lesson_url = url(back_day, "/lesson/" + lesson_id)
    raw_path = ROOT + "/lesson/" + lesson_id
    cancelled = bool(published and published["cancelled"])
    hidden_for_me = raspay.hidden_versions(db, user, selection).get(lesson_id) == event.version
    span = duration(int((end - start).total_seconds() // 60))
    body = subhead(WEEKDAYS[day.weekday()], url(back_day), day_label(day))
    body += (f'<article class="rp-detail"><div class="rp-detail-top">{kind_badge(event.kind, event.subject, prefs)}'
             f'<span>{e(span)}</span></div><h2>{e(event.subject)}</h2><div class="rp-detail-times">'
             f'<div><span class="rp-kicker">Начало</span><time datetime="{e(starts)}">{e(time_label(starts, event.timezone, day))}</time></div>'
             f'<div><span class="rp-kicker">Конец</span><time datetime="{e(ends)}">{e(time_label(ends, event.timezone, day))}</time></div></div></article>')
    if cancelled:
        body += '<p class="rp-status" role="status">Пара отменена источником</p>'
    elif in_force:
        room = " · " + saved["room"] if saved["room"] else ""
        body += f'<p class="rp-status" role="status">Личная поправка действует{e(room)}</p>'
    elif saved:
        body += '<p class="rp-status" role="status">Источник изменился. Сверь личную поправку.</p>'
    if not cancelled and not published:
        body += lesson_route.banner(event.room, lesson_url, explain_unmapped=True)
    body += sources(request, event, published)
    if prefs.room and raspay.english(event.subject, event.subject_id, demonstration=not selection):
        rooms = {name for name, _ in json.loads(published["rooms_json"])} if published else {event.room}
        if prefs.room in rooms:
            body += f'<p class="rp-muted">Моя аудитория по английскому · {e(prefs.room)}</p>'
    body += notes(request, db, event, back_day)
    base_href = "/schedule/public/" if selection else "/schedule/"
    subject = urlencode({"subject": event.subject_id}) if event.subject_id and not selection else ""
    personal = link("/tasks/new" + ("?" + subject if subject else ""), "Добавить задачу")
    body += f'<nav class="rp-links" aria-label="Действия учебного кабинета">{personal} '
    body += link(base_href + event.id, "Обычная карточка пары") + "</nav>"
    body += '<h2 class="rp-section-title">Если что-то не так</h2><div class="rp-lesson-actions">'
    if published and not cancelled:
        body += link(url(back_day, f"/lesson/{event.id}/time"), "Неверное время", "rp-outline")
    if not cancelled:
        action = "restore" if hidden_for_me else "hide"
        label = "Вернуть в мой список" if hidden_for_me else "Скрыть у себя"
        focus = ' data-restore-focus' if request.query("focus") == action and not screen else ""
        body += (f'<a href="{e(url(back_day, f"/lesson/{event.id}/{action}"))}" '
                 f'class="rp-outline rp-danger"{focus}>{e(label)}</a>')
    body += "</div>"
    fields = (hidden("date", back_day.isoformat()) + hidden("source_version", event.version)
              + hidden("context_revision", study_context.preference(db, user)[1]))
    if screen == "time" and published and not cancelled:
        starts_field = input_field("starts", "Начало", start.strftime("%Y-%m-%dT%H:%M"), "required", "datetime-local")
        ends_field = input_field("ends", "Конец", end.strftime("%Y-%m-%dT%H:%M"), "required", "datetime-local")
        fields += hidden("revision", saved["revision"] if saved else 0)
        fields += hidden("room", saved["room"] if saved else event.room or "")
        edit = form(request, raw_path + "/time", fields + starts_field + ends_field
                    + button("Исправить расписание"), command=True, style="rp-edit-time")
        if saved:
            edit += form(request, raw_path + "/time/remove", fields + button("Убрать поправку"), command=True)
        body += overlay("Когда идёт пара?", "Поправка применяется только к этому занятию в твоём календаре.",
                        edit, lesson_url)
    if screen in {"hide", "restore"} and not cancelled:
        restore = screen == "restore"
        title = "Вернуть пару?" if restore else "Скрыть пару?"
        description = "Пара снова появится в списке." if restore else (
            "Пара скроется только в твоём представлении Raspay. Её можно вернуть из списка скрытых пар.")
        controls = form(request, raw_path + "/" + screen, fields + button("Вернуть пару" if restore else "Скрыть пару"))
        body += overlay(title, description, controls, lesson_url + "&" + urlencode({"focus": screen}))
    return shell(request, event.subject, body)


def overlay(title, description, controls, back_to):
    return (f'<dialog open class="rp-sheet rp-compact-sheet" data-rp-dialog aria-labelledby="rp-dialog-title">'
            f'<h2 id="rp-dialog-title">{e(title)}</h2><p class="rp-muted">{e(description)}</p>{controls}'
            f'<a class="rp-outline" autofocus data-rp-close href="{e(back_to)}">Отмена</a></dialog>')
