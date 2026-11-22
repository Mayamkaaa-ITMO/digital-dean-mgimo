from datetime import date, datetime, timedelta
import json
from zoneinfo import ZoneInfo

from . import calendar_tools as calendar, public_schedule, study_context
from .clock import now
from .domain import role
from .learning import optional_int
from .schedule import local_value
from .ui import back, button, day_label, e, empty, form, hidden, input_field, link, local_label, page, panel, select_field


def field_time(value, zone):
    return local_value(value, zone).strftime("%Y-%m-%dT%H:%M")


def override_panel(request, db, row, zone):
    saved = calendar.override(db, request.user, row["id"])
    path = f'/schedule/public/{row["id"]}/adjustment'
    fields = hidden("source_version", row["version"]) + hidden("revision", saved["revision"] if saved else 0)
    text = "<p>Личная поправка действует только в твоём календаре.</p>"
    if saved and saved["base_version"] != row["version"]:
        text += '<p role="status">Источник изменился. Сверь время и подтверди поправку или убери её.</p>'
    if saved:
        text += form(request, path + "/remove", fields + button("Убрать поправку"), command=True)
    if not row["cancelled"]:
        values = saved or row
        editable = input_field("starts", "Начало", field_time(values["starts_at"], zone), "required", "datetime-local")
        editable += input_field("ends", "Конец", field_time(values["ends_at"], zone), "required", "datetime-local")
        rooms = ", ".join(room[0] for room in json.loads(row["rooms_json"]))
        editable += input_field("room", "Аудитория", saved["room"] if saved else rooms, 'maxlength="120"')
        text += form(request, path + "/save", fields + editable + button("Сохранить личную поправку"), command=True)
    return panel("Личная поправка", text)


def describe(value, zone):
    title, kind, start, end, rooms, cancelled = json.loads(value)
    place = ", ".join(item[0] for item in json.loads(rooms))
    return " · ".join(filter(None, (title, kind, local_label(start, zone), local_label(end, zone), place,
                                    "Отменено" if cancelled else "")))


def demo_changes(request, db):
    user = request.user
    rows = db.execute("""
        SELECT e.id,s.name AS subject,e.starts_at,e.ends_at,e.room,e.timezone,r.old_starts_at,r.old_ends_at,r.old_room,r.changed_at
        FROM schedule_event e JOIN schedule_revision r ON r.event_id=e.id
            AND r.university_id=e.university_id AND r.version=e.version
        JOIN subject s ON s.id=e.subject_id AND s.university_id=e.university_id
        WHERE e.university_id=? AND e.group_id=? AND e.period_id=?
        ORDER BY r.changed_at DESC,e.id LIMIT 100
    """, (user["university_id"], user["group_id"], user["period_id"]))
    items = []
    for row in rows:
        zone = row["timezone"]
        before = " · ".join((local_label(row["old_starts_at"], zone), local_label(row["old_ends_at"], zone),
                            row["old_room"] or "Аудитория не указана"))
        after = " · ".join((local_label(row["starts_at"], zone), local_label(row["ends_at"], zone),
                           row["room"] or "Аудитория не указана"))
        items.append(f'<li><div>{link("/schedule/" + row["id"], row["subject"])}'
                     f'<p>Было: {e(before)}</p><p>Стало: {e(after)}</p>'
                     f'<p class="meta">{e(local_label(row["changed_at"], zone))}</p></div></li>')
    content = f'<ul class="rows">{"".join(items)}</ul>' if items else empty("Переносов в текущем семестре пока нет.")
    return page(request, "Изменения расписания", back("/schedule", "К расписанию") + panel("Учебное демо", content))


def changes(request, db):
    role(request.user, "student")
    selection, user = request.public, request.user
    if not selection:
        return demo_changes(request, db)
    zone = public_schedule.scope(selection).timezone
    revision = hidden("context_revision", study_context.preference(db, user)[1])
    watched = calendar.is_followed(db, user, selection)
    body = back("/schedule", "К расписанию")
    body += form(request, "/schedule/changes", revision + hidden("enabled", "0" if watched else "1")
                 + button("Отписаться от изменений" if watched else "Следить за этой группой"))
    rows = db.execute("""
        SELECT c.*,e.subject FROM public_lesson_change c JOIN public_lesson e ON e.id=c.lesson_id
        WHERE e.source_id=? AND e.group_id=? ORDER BY c.detected_at DESC,c.lesson_id,c.version DESC LIMIT 100
    """, selection).fetchall()
    items = ""
    for row in rows:
        old = describe(row["old_json"], zone) if row["old_json"] else "Новая запись"
        new = describe(row["new_json"], zone)
        _, _, start, end, _, cancelled = json.loads(row["new_json"])
        collision = ""
        if not cancelled and calendar.busy(db, user, selection, start, end, visibility="personal"):
            collision = "<p>По новому времени есть пересечение с личным календарём.</p>"
        reason = {"snapshot": "снимок", "live": "ответ источника", "synthetic": "проверочная версия", "rollback": "откат опубликованной версии"}[row["provenance"]]
        items += (f'<li><div>{link("/schedule/public/" + row["lesson_id"], row["subject"])}'
                  f'<p>Было: {e(old)}</p><p>Стало: {e(new)}</p>'
                  f'<p class="meta">{e(local_label(row["detected_at"], zone))} · {reason}</p>{collision}</div></li>')
    body += panel("История группы", f'<ul class="rows">{items}</ul>' if items else empty("Зафиксированных изменений пока нет."))
    return page(request, "Изменения расписания", body)


def free_time(request, db):
    user, selection = request.user, request.public
    role(user, "student")
    zone = calendar.period_for(user, selection).timezone
    today = now().astimezone(ZoneInfo(zone)).date()
    first, last = request.query("first", today.isoformat()), request.query("last", (today+timedelta(days=6)).isoformat())
    raw_start, raw_end = request.query("from", "08:00"), request.query("to", "22:00")
    raw_minutes = request.query("minutes", "60")
    visibility = request.query("visibility", "all")
    fields = input_field("first", "Первый день", first, "required", "date")
    fields += input_field("last", "Последний день", last, "required", "date")
    fields += input_field("from", "После", raw_start, "required", "time")
    fields += input_field("to", "До", raw_end, "required", "time")
    fields += input_field("minutes", "Минут", raw_minutes, 'min="15" max="720" required', "number")
    fields += select_field("visibility", "Учитывать", list(calendar.VISIBILITY.items()), visibility)
    body = back("/schedule", "К расписанию") + '<form method="get" action="/schedule/free">' + fields + button("Рассчитать") + "</form>"
    if request.query("first"):
        first, last, start, end = calendar.window(first, last, raw_start, raw_end, visibility)
        minutes = optional_int(raw_minutes, "Длительность", 720)
        if minutes is None or minutes < 15:
            from .domain import DomainError
            raise DomainError(422, "Укажи длительность от 15 минут.")
        items = calendar.calendar(db, user, selection, first, last+timedelta(days=1))
        sections = []
        revision = study_context.preference(db, user)[1]
        for day, low, high in calendar.free_windows(items, first, last, start, end, zone, visibility):
            stop = datetime.fromisoformat(low) + timedelta(minutes=minutes)
            if stop > datetime.fromisoformat(high):
                continue
            fields = hidden("starts", low) + hidden("ends", stop.isoformat(timespec="seconds"))
            fields += hidden("context_revision", revision) + hidden("visibility", visibility)
            fields += input_field("title", "Название события", "Личное событие", 'maxlength="120" required')
            sections.append(f'<li><div>{e(day_label(day))} · {e(field_time(low, zone)[11:])}–'
                            f'{e(field_time(high, zone)[11:])}'
                            + form(request, "/schedule/free", fields + button("Добавить"), command=True)
                            + "</div></li>")
            if len(sections) == 30:
                break
        coverage = ("Публичный снимок покрывает только подтверждённые даты и события. " if selection else "")
        coverage += "При добавлении учитываются все известные личные интервалы и выбранное расписание."
        body += panel("Свободно по выбранным данным", f'<p>{e(coverage)}</p>'
                      + (f'<ul class="rows">{"".join(sections)}</ul>' if sections else empty("Подходящих интервалов нет.")))
    return page(request, "Свободное время", body, zone)
