"""Calendar presentation shared by the agenda and the weekly timetable."""
from . import appearance
from .schedule import local_value
from .ui import MONTHS, SHORT_DAYS, WEEKDAYS, day_label, e


def item_href(item, context):
    return f"{item.href}?{context}" if context and item.lesson_kind else item.href


def period_label(first, last):
    if first == last:
        return day_label(first)
    if first.month == last.month and first.year == last.year:
        return f"{first.day}–{last.day} {MONTHS[last.month - 1]}"
    return f"{day_label(first)} — {day_label(last)}"


def day_count(items, missing):
    if missing:
        return "Данные не получены"
    lessons = sum(item.kind == "lesson" for item in items)
    if lessons == len(items):
        return appearance.count_label(lessons, "пара", "пары", "пар") if lessons else "Пар нет"
    return appearance.count_label(len(items), "событие", "события", "событий")


def preview_attributes(item, instant, time_range, event_type, notes=(), *, old_room="", context=""):
    from .activity_extensions import lesson_map_url

    room_url = ""
    if item.href.startswith("/schedule/") and not item.href.startswith("/schedule/public/"):
        room_url = lesson_map_url(item.room, item.href.removeprefix("/schedule/"), context=context)
    return (f' data-lesson-preview data-time="{e(time_range)}" data-date="{e(day_label(instant.date()))}"'
            f' data-kind="{e(event_type)}" data-status="{e(" · ".join(notes))}"'
            f' data-room="{e(room_url)}" data-location="{e(item.room)}" data-teacher="{e(item.teacher)}"'
            f' data-old-room="{e(old_room)}"')


def event_row(item, href, day, zone, overlap, *, position="", continues=False, old_room=""):
    start = local_value(item.start, zone)
    stop = local_value(item.end, zone) if item.end else None
    end_label = stop.strftime("%H:%M" if stop.date() == start.date() else "%d.%m %H:%M") if stop else ""
    end = f'<time datetime="{e(item.end)}">{end_label}</time>' if stop and not item.all_day else ""
    first = "Весь день" if item.all_day else "Продолжается" if continues else f"{start:%H:%M}"
    labels = {
        "exam": "Экзамен", "task": "Срок задачи", "event": "Событие", "booking": "Бронь или запись",
    }
    event_type = item.lesson_kind or labels.get(item.kind, "Занятие")
    badge = appearance.lesson_type(event_type)
    notes = []
    if item.changed:
        notes.append("Перенос аудитории" if old_room else "Изменено")
    if overlap:
        notes.append("Пересечение")
    if item.lesson_status:
        notes.append(item.lesson_status)
    status = "".join(f'<span class="dean-schedule-note">{e(note)}</span>' for note in notes)
    detail = []
    if item.lesson_kind:
        room = item.room or "Аудитория не указана"
        room_number = room.rsplit(" · ", 1)[-1]
        if item.teacher:
            detail.append(f'<span class="dean-schedule-teacher">{e(item.teacher)}</span>')
        detail.append(f'<span class="dean-schedule-place" title="{e(room)}">'
                      f'{appearance.icon("map")}<span class="dean-schedule-place-full">{e(room)}</span>'
                      f'<span class="dean-schedule-place-compact" aria-hidden="true">{e(room_number)}</span></span>')
    elif item.subtitle and item.subtitle != labels.get(item.kind):
        detail.append(f'<span>{e(item.subtitle)}</span>')
    placement = f' data-schedule-position="{position}"' if position else ""
    description_id = e(f"schedule-{day.isoformat()}-{item.key}")
    time_range = f"{first}–{end_label}" if end else first
    description = " · ".join(part for part in (time_range, event_type, item.room or item.teacher, *notes) if part)
    preview = ""
    if item.kind == "lesson":
        preview = preview_attributes(item, start, time_range, event_type, notes,
                                     old_room=old_room, context=href.partition("?")[2])
    return (f'<li data-event="{e(item.kind)}" class="dean-schedule-entry"{placement}>'
            f'<a href="{e(href)}" aria-label="{e(item.title)}" title="{e(item.title)}" aria-describedby="{description_id}"{preview}>'
            f'<span class="dean-schedule-time"><time datetime="{e(item.start)}">{first}</time>{end}</span>'
            f'<span class="dean-schedule-item"><span class="dean-schedule-badges">{badge}{status}</span>'
            f'<strong>{e(item.title)}</strong><span class="dean-schedule-meta">{"".join(detail)}</span></span>'
            f'{appearance.icon("chevron-right")}</a>'
            f'<span class="dean-visually-hidden" id="{description_id}">{e(description)}</span></li>')


def gap_label(minutes):
    hours, rest = divmod(minutes, 60)
    return " ".join(part for part in (f"{hours} ч" if hours else "", f"{rest} мин" if rest else "") if part)


def day_section(day, items, today, focus, href, context, zone, overlapping, message, missing, *,
                gaps=False, bounds=None, old_rooms=None):
    full = WEEKDAYS[day.weekday()]
    short = f"{SHORT_DAYS[day.weekday()]} · {day.day}"
    attrs = (' data-today' if day == today else '') + (' data-focused' if day == focus else '')
    count = day_count(items, missing)
    headline = (f'<header class="dean-schedule-day-head"><h2><a href="{e(href)}" aria-label="{full}, {day_label(day)}">'
                f'<span class="dean-schedule-day-full">{full}</span>'
                f'<span class="dean-schedule-day-short">{short}</span>'
                f'</a></h2><p><time datetime="{day.isoformat()}">{day_label(day)}</time><span>{e(count)}</span></p></header>')
    old_rooms = old_rooms or {}
    all_day = "".join(event_row(item, item_href(item, context), day, zone, item.key in overlapping,
                                old_room=old_rooms.get(item.key, ""))
                      for item in items if item.all_day)
    timed = [item for item in items if not item.all_day]
    positions = {}
    if bounds:
        first, last = bounds
        for item, start, stop, lane, lanes in positioned_events(day, timed, zone):
            positions[item.key] = f"{start-first} {min(stop, last)-start} {lane} {lanes}"
    rows = []
    previous_end = None
    previous_was_lesson = False
    for item in timed:
        is_lesson = item.kind == "lesson" and bool(item.end)
        if gaps and is_lesson and previous_was_lesson and previous_end and item.start > previous_end:
            start, end = local_value(previous_end, zone), local_value(item.start, zone)
            minutes = int((end - start).total_seconds() // 60)
            if minutes >= 45 and start.date() == end.date() == day:
                rows.append(f'<li class="dean-schedule-gap"><span>{appearance.icon("calendar")}Перерыв {gap_label(minutes)}</span>'
                            f'<span>{start:%H:%M}–{end:%H:%M}</span></li>')
        rows.append(event_row(item, item_href(item, context), day, zone, item.key in overlapping,
                              position=positions.get(item.key, ""), continues=local_value(item.start, zone).date() < day,
                              old_room=old_rooms.get(item.key, "")))
        stop = item.end or item.start
        previous_end = max(stop, previous_end or stop)
        previous_was_lesson = is_lesson
    empty = f'<p class="dean-schedule-day-empty">{e(message)}</p>' if not items else ""
    if missing and items:
        empty = '<p class="dean-schedule-day-empty">Расписание группы на этот день ещё не получено.</p>'
    return (f'<section class="dean-schedule-day"{attrs}>{headline}'
            f'<ul class="dean-schedule-all-day">{all_day}</ul>'
            f'<div class="dean-schedule-day-track"><ul class="dean-schedule-events">{"".join(rows)}</ul>'
            f'{empty}</div></section>')


def minute_of_day(value, day, zone):
    local = local_value(value, zone)
    if local.date() != day:
        return 0 if local.date() < day else 24 * 60
    return local.hour * 60 + local.minute


def positioned_events(day, items, zone):
    """Allocate overlapping cards side by side within their shared time interval."""
    cluster, lanes = [], []
    starts = sorted(((minute_of_day(item.start, day, zone), item) for item in items), key=lambda event: event[0])
    for start, item in starts:
        end = minute_of_day(item.end, day, zone) if item.end else start
        stop = min(24 * 60, max(end, start + 40))
        if lanes and start >= max(lanes):
            yield from ((*event, len(lanes)) for event in cluster)
            cluster, lanes = [], []
        lane = next((index for index, until in enumerate(lanes) if until <= start), len(lanes))
        lanes[lane:lane + 1] = [stop]
        cluster.append((item, start, stop, lane))
    yield from ((*event, len(lanes)) for event in cluster)


def week_axis(days, zone):
    timed = [(minute_of_day(item.start, day, zone),
              minute_of_day(item.end, day, zone) if item.end else minute_of_day(item.start, day, zone))
             for day, items in days for item in items if not item.all_day]
    first = min((start for start, _ in timed), default=9 * 60) // 60 * 60
    end = max((max(start + 40, stop) for start, stop in timed), default=16 * 60)
    last = min(24 * 60, (end + 59) // 60 * 60)
    ticks = "".join(f"<span>{minute // 60:02}:00</span>" for minute in range(first, last, 60))
    return (first, last), f'<div class="dean-schedule-axis" aria-hidden="true">{ticks}</div>'


def month_cell(day, items, today, focus, href, missing):
    lesson_count = sum(item.kind == "lesson" for item in items)
    event_count = len(items) - lesson_count
    events = appearance.count_label(event_count, "событие", "события", "событий") if event_count else ""
    if lesson_count:
        tone = "lessons"
        text = appearance.count_label(lesson_count, "пара", "пары", "пар")
        status = ", ".join(part for part in (text, events) if part)
    elif event_count:
        tone, text, status = "events", events, events
    else:
        tone, text = ("unavailable", "Нет данных") if missing else ("empty", "Пар нет")
        status = "Данные на этот день не получены." if missing else text
    if missing and items:
        status += ". Учебное расписание на этот день не получено."
    attrs = (' data-today aria-current="date"' if day == today else '') + (' data-focused' if day == focus else '')
    attrs += ' data-scheduled' if items else ''
    attrs += ' data-unavailable' if missing else ''
    return (f'<a href="{e(href)}" aria-label="Открыть день, {WEEKDAYS[day.weekday()]}, {day_label(day)} · {e(status)}"'
            f' class="dean-schedule-month-cell" data-day-tone="{tone}"{attrs}>'
            f'<time datetime="{day.isoformat()}">{day.day}</time><span>{e(text)}</span></a>')


def month_legend(has_missing):
    items = [("lessons", "Есть пары"), ("events", "Другие события"), ("empty", "Свободный день")]
    if has_missing:
        items.append(("unavailable", "Нет данных"))
    return '<ul class="dean-schedule-month-legend" aria-label="Обозначения в календаре">' + "".join(
        f'<li data-day-tone="{tone}">{label}</li>' for tone, label in items
    ) + '</ul>'
