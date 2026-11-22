"""A second view of the existing agenda, using the team's Raspay layout."""

from datetime import timedelta
from urllib.parse import urlencode
from zoneinfo import ZoneInfo

from . import agenda, calendar_tools, public_schedule, public_views, raspay, services, study_context
from .clock import now
from .domain import DomainError, role, timestamp
from .schedule import local_midnight, local_value
from .ui import SHORT_DAYS, WEEKDAYS, button, day_label, e, form, hidden, input_field, link, page

ROOT = "/schedule/raspay"


def url(day=None, screen=""):
    return ROOT + screen + ("?" + urlencode({"date": day.isoformat()}) if day else "")


def original(day):
    return "/schedule?" + urlencode({"view": "day", "date": day.isoformat()})


def shell(request, title, body):
    return page(request, title, body, section="raspay", raspay=True)


def dates(request, db, selection):
    role(request.user, "student")
    zone = calendar_tools.period_for(request.user, selection).timezone
    today = now().astimezone(ZoneInfo(zone)).date()
    selected = request.query("date")
    if selected:
        return today, study_context.date_value(selected), zone
    focus = study_context.focus_day(db, request.user, selection, today)
    if selection and focus == today and not public_schedule.public_week(db, selection, today):
        snapshot = db.execute("""
            SELECT monday FROM public_week WHERE source_id=? AND group_id=? AND revision>0
            ORDER BY monday DESC LIMIT 1
        """, selection).fetchone()
        if snapshot:
            focus = study_context.date_value(snapshot["monday"])
    return today, focus, zone


def context_label(db, user, selection):
    if selection:
        university, group = public_views.scope_label(selection).rsplit(" · ", 1)
        return university, group
    return "Учебное демо", services.profile(db, user)["group_name"]


def intersects_day(item, day, zone):
    start = local_midnight(day, zone)
    stop = local_midnight(day + timedelta(days=1), zone)
    return item.start < stop and (item.end > start if item.end else item.start >= start)


def count_label(n):
    if n == 0:
        return "пар нет"
    word = "пар"
    if not 11 <= n % 100 <= 14:
        if n % 10 == 1:
            word = "пара"
        elif 2 <= n % 10 <= 4:
            word = "пары"
    return f"{n} {word}"


def dot(color):
    return (f'<svg class="rp-dot" width="8" height="8" viewBox="0 0 8 8" aria-hidden="true">'
            f'<circle cx="4" cy="4" r="4" fill="{e(color)}"/></svg>')


def kind_badge(name, subject, prefs):
    key = raspay.color_key(name, subject)
    label = raspay.PALETTES["sport"][0] if key == "sport" else name
    return f'<span class="rp-kind">{dot(prefs.color(key))}{e(label)}</span>'


def duration(minutes):
    hours, rest = divmod(minutes, 60)
    return " ".join(p for p in (f"{hours} ч" if hours else "", f"{rest} мин" if rest or not hours else "") if p)


def time_label(value, zone, day):
    local = local_value(value, zone)
    return local.strftime("%H:%M" if local.date() == day else "%d.%m %H:%M")


def item_href(item, day):
    return url(day, "/lesson/" + item.key.split(":", 1)[1]) if item.lesson_kind else item.href


def day_tabs(first, today, focus, classes, zone):
    cells = []
    for offset in range(7):
        day = first + timedelta(days=offset)
        current = ' aria-current="date"' if day == focus else ""
        quiet = "" if any(intersects_day(item, day, zone) for item in classes) else " rp-pill-quiet"
        label = "Сегодня" if day == today else f"{SHORT_DAYS[day.weekday()]} {day.day}"
        cells.append(f'<a class="rp-pill{quiet}" href="{e(url(day))}"{current} '
                     f'aria-label="{e(WEEKDAYS[day.weekday()] + ", " + day_label(day) + (" (сегодня)" if day == today else ""))}">'
                     f'{e(label)}</a>')
    return '<nav class="rp-pills" aria-label="Дни расписания">' + "".join(cells) + "</nav>"


def now_card(classes, focus, today, zone):
    if focus != today or not classes:
        return ""
    current = timestamp()
    active = next((item for item in classes if item.start <= current < item.end), None)
    upcoming = next((item for item in classes if item.start > current), None)
    item = active or upcoming
    if item is None:
        return '<div class="rp-now"><span class="rp-kicker">Сегодня</span><p class="rp-now-title">Пары закончились</p></div>'
    if active:
        caption, title, deadline, start = "Сейчас идёт", item.title, item.end, item.start
    else:
        first = not any(lesson.end <= current for lesson in classes)
        caption = "Сегодня" if first else "Перерыв"
        title = (f'{"Первая пара" if first else "Следующая пара"} — {item.title}, '
                 f'{item.lesson_kind.lower()} в {time_label(item.start, zone, focus)}')
        deadline = item.start
        start = max((lesson.end for lesson in classes if lesson.end <= current), default=None)
    ring = ""
    if start:
        ring = (f'<div class="rp-timer" data-rp-counter="{e(deadline)}" data-rp-start="{e(start)}">'
                '<svg width="72" height="72" viewBox="0 0 72 72" aria-hidden="true">'
                '<circle class="rp-ring-track" cx="36" cy="36" r="31"/>'
                '<circle class="rp-ring-value" cx="36" cy="36" r="31" pathLength="100"/></svg>'
                '<span data-rp-remaining></span></div>')
    room = f'<span class="rp-room">↗ {e(item.room)}</span>' if item.room else ""
    return (f'<a class="rp-now" href="{e(item_href(item, focus))}"><div><span class="rp-kicker">{e(caption)}</span>'
            f'<p class="rp-now-title">{e(title)}</p>{room}</div>{ring}</a>')


def timeline(lessons, day, zone, prefs, overlapping, *, end_marker=True):
    rows, previous_end = [], None
    for item in lessons:
        if previous_end and prefs.show_gaps and previous_end < item.start:
            gap = local_value(item.start, zone) - local_value(previous_end, zone)
            gap_text = duration(int(gap.total_seconds() // 60))
            rows.append(f'<li class="rp-row rp-gap"><span>{gap_text}</span><i></i><span>Перерыв между парами</span></li>')
        tags = (item.lesson_status, "Пересечение" if item.key in overlapping else "", "Изменено" if item.changed else "")
        badges = " · ".join(filter(None, tags))
        meta = (f'<span>♟ {e(item.teacher)}</span>' if item.teacher else "")
        if item.room:
            meta += f'<span>↗ {e(item.room)}</span>'
        elif item.lesson_kind:
            meta += '<span>Аудитория не указана</span>'
        if prefs.room in item.rooms and raspay.english(
            item.title, item.subject_id, demonstration=not item.href.startswith("/schedule/public/")
        ):
            meta += f'<span>Моя аудитория · {e(prefs.room)}</span>'
        content = (f'{kind_badge(item.lesson_kind, item.title, prefs)}<strong>{e(item.title)}</strong>'
                   f'<span class="rp-lesson-meta">{meta}{e(badges)}</span>')
        rows.append(f'<li><a class="rp-row rp-lesson" href="{e(item_href(item, day))}">'
                    f'<span class="rp-times"><time datetime="{e(item.start)}">{e(time_label(item.start, zone, day))}</time>'
                    f'<time datetime="{e(item.end)}">{e(time_label(item.end, zone, day))}</time></span>'
                    f'<i class="rp-axis"></i><span class="rp-lesson-content">{content}</span></a></li>')
        previous_end = max(item.end, previous_end) if previous_end else item.end
    if end_marker and previous_end:
        rows.append(f'<li class="rp-row rp-end"><time datetime="{e(previous_end)}">{e(time_label(previous_end, zone, day))}'
                    '</time><i></i><span>Дальше пар нет</span></li>')
    return '<ol class="rp-timeline">' + "".join(rows) + "</ol>"


def other_items(items, focus, zone, overlapping):
    if not items:
        return ""
    rows = []
    for item in items:
        time = "Весь день" if item.all_day else time_label(item.start, zone, focus)
        if item.end and not item.all_day:
            time += "–" + time_label(item.end, zone, focus)
        conflict = " · Пересечение" if item.key in overlapping else ""
        rows.append(f'<li>{link(item.href, item.title)}<span>{e(time + " · " + item.subtitle + conflict)}</span></li>')
    return '<section class="rp-other"><h2 class="rp-divider">Личный календарь и сроки</h2><ul>' + "".join(rows) + "</ul></section>"


def modes(day):
    entries = [link(original(day), "Исходный интерфейс"), link(url(day, "/groups"), "Вуз и группа")]
    entries.extend(link("/schedule?" + urlencode({"date": day.isoformat(), "view": mode}), label)
                   for mode, label in (("week", "Неделя"), ("month", "Месяц"), ("list", "Список")))
    return '<nav class="rp-links" aria-label="Представления и виды расписания">' + " ".join(entries) + "</nav>"


def source_info(db, selection, focus):
    if not selection:
        return '<p class="rp-muted">Учебные демоданные</p>'
    return ('<details class="rp-source"><summary>Источник и покрытие</summary>'
            + public_views.info(db, selection, focus) + "</details>")


def day_view(request, db, *, settings_open=False):
    user, selection = request.user, request.public
    today, focus, zone = dates(request, db, selection)
    prefs = raspay.preference(db, user, selection)
    strip_start = today if today <= focus < today + timedelta(days=7) else focus - timedelta(days=focus.weekday())
    last = strip_start + timedelta(days=7)
    items = calendar_tools.calendar(db, user, selection, strip_start, last)
    hidden_versions = raspay.hidden_versions(db, user, selection)
    visible, concealed, other = [], [], []
    for item in items:
        if item.lesson_kind:
            bucket = concealed if hidden_versions.get(item.key.split(":", 1)[1]) == item.version else visible
        else:
            bucket = other
        bucket.append(item)
    lessons = [item for item in visible if intersects_day(item, focus, zone)]
    hidden_lessons = [item for item in concealed if intersects_day(item, focus, zone)]
    marker = public_schedule.public_week(db, selection, focus) if selection else None
    known = not selection or public_schedule.covered_day(selection, focus, marker)
    parity = public_schedule.week_scope(*selection, marker["monday"]).info.get("parity") if marker else None
    weekday = WEEKDAYS[focus.weekday()]
    summary = day_label(focus) + " · " + (count_label(len(lessons)) if known or lessons else "данных нет")
    if parity:
        summary += f" · {parity} неделя"
    cog = '<svg width="22" height="22" viewBox="0 0 24 24" aria-hidden="true"><path fill="currentColor" d="M10 2h4l.5 2.4 2 .9 2.1-1.4 2.8 2.8-1.4 2.1.9 2L23 11v4l-2.3.4-.9 2 1.4 2.1-2.8 2.8-2.1-1.4-2 .9L14 24h-4l-.4-2.2-2-.9-2.1 1.4-2.8-2.8 1.4-2.1-.9-2L1 15v-4l2.2-.4.9-2L2.7 6.5l2.8-2.8 2.1 1.4 2-.9L10 2zm2 7a4 4 0 100 8 4 4 0 000-8z"/></svg>'
    body = (f'<div class="rp-head"><div><h1>{weekday}</h1><p class="rp-muted">{e(summary)}</p></div>'
            f'<a class="rp-icon" aria-label="Настройки" href="{e(url(focus, "/settings"))}">{cog}</a></div>')
    body += day_tabs(strip_start, today, focus, visible, zone)
    if not known:
        body += '<p class="rp-empty">Данные на этот день не получены.</p>'
    conflicting = agenda.conflicts(sorted(visible + other, key=lambda item: (item.start, item.key)))
    if lessons:
        body += now_card(lessons, focus, today, zone)
        body += '<h2 class="rp-divider">Расписание</h2>'
        past = [item for item in lessons if focus == today and item.end <= timestamp()]
        if past and len(past) < len(lessons):
            body += ('<details class="rp-past"><summary>Прошедшие пары</summary>'
                     + timeline(past, focus, zone, prefs, conflicting, end_marker=False) + "</details>")
        remaining = [item for item in lessons if item not in past]
        body += timeline(remaining or lessons, focus, zone, prefs, conflicting)
    elif known:
        body += f'<p class="rp-empty">{"Все пары скрыты" if hidden_lessons else "В этот день пар нет"}</p>'
    if hidden_lessons:
        previews = "".join(f"<li>{link(item_href(item, focus), item.title)}</li>" for item in hidden_lessons)
        body += f'<details class="rp-other"><summary>Скрытые пары</summary><ul>{previews}</ul></details>'
    body += other_items([item for item in other if intersects_day(item, focus, zone)], focus, zone, conflicting)
    body += ('<div class="rp-period">' + link(url(focus - timedelta(days=1)), "Предыдущий день")
             + link(url(today), "Сегодня") + link(url(focus + timedelta(days=1)), "Следующий день") + "</div>")
    body += modes(focus) + f'<p class="rp-muted">Часовой пояс · {e(zone)}</p>' + source_info(db, selection, focus)
    if settings_open:
        body += settings_sheet(request, db, focus, prefs, parity)
    return shell(request, weekday, body)


def settings(request, db):
    return day_view(request, db, settings_open=True)


def section_title(label):
    return f'<h2 class="rp-section-title">{e(label)}</h2>'


def settings_fields(request, db, day, prefs):
    return (hidden("date", day.isoformat()) + hidden("revision", prefs.revision)
            + hidden("context_revision", study_context.preference(db, request.user)[1]))


def settings_sheet(request, db, focus, prefs, parity):
    university, group = context_label(db, request.user, request.public)
    badge = f'<span class="rp-week"><span class="rp-kicker">Неделя</span><b>{e(parity.capitalize())}</b></span>' if parity else ""
    fields = settings_fields(request, db, focus, prefs) + hidden("show_gaps", "0" if prefs.show_gaps else "1")
    toggle = (f'<button class="rp-setting" type="submit" role="switch" aria-checked="{str(prefs.show_gaps).lower()}">'
              '<span><strong>Показывать окна</strong><small>Перерывы между парами в списке</small></span>'
              f'<i class="rp-toggle{" on" if prefs.show_gaps else ""}" aria-hidden="true"></i></button>')
    return (f'<dialog open class="rp-sheet" aria-labelledby="rp-settings-title" data-rp-dialog>'
            f'<div class="rp-sheet-head"><h2 id="rp-settings-title">Настройки</h2>'
            f'<a href="{e(url(focus))}" class="rp-icon rp-close" data-rp-close aria-label="Закрыть">×</a></div>'
            f'<div class="rp-group-info"><div><span class="rp-kicker">Моя группа</span><strong>{e(group)}</strong>'
            f'<span class="rp-muted">{e(university)}</span></div>{badge}</div>'
            + section_title("Расписание") + setting_link(url(focus, "/room"), "Личная аудитория для иностранного языка",
                                                        prefs.room or "Не выбрана")
            + section_title("Вид расписания") + form(request, ROOT + "/preferences/gaps", fields + toggle)
            + setting_link(url(focus, "/colors"), "Цвета типов занятий", "Лекция, практика, спорт, лабораторная и НИР")
            + section_title("Учебный контекст") + setting_link(url(focus, "/groups"), "Сменить группу", university)
            + setting_link(original(focus), "Исходный интерфейс", "Неделя, месяц, фильтры, импорт и экспорт")
            + setting_link("/support", "Поддержка", "Связаться с командой деканата") + "</dialog>")


def setting_link(href, label, subtitle):
    return (f'<a class="rp-setting" href="{e(href)}"><span><strong>{e(label)}</strong>'
            f'<small>{e(subtitle)}</small></span><span class="rp-chevron" aria-hidden="true">›</span></a>')


def subhead(title, back_to, subtitle=""):
    sub = f'<p class="rp-muted">{e(subtitle)}</p>' if subtitle else ""
    return (f'<div class="rp-head rp-head-back"><a class="rp-icon" href="{e(back_to)}" aria-label="Назад">‹</a>'
            f'<div><h1>{e(title)}</h1>{sub}</div></div>')


def groups(request, db):
    _, focus, _ = dates(request, db, request.public)
    revision = hidden("revision", study_context.preference(db, request.user)[1])
    demo_group = services.profile(db, request.user)["group_name"]
    body = subhead("Выберите группу", url(focus), "Нажми на нужную группу")
    sources = [("", "Учебное демо", {"": {"label": demo_group}})]
    sources.extend((source["source_id"], public_schedule.source_label(source), source["groups"]) for source in public_schedule.SOURCES.values())
    for source, name, choices in sources:
        cards = []
        for group, units in choices.items():
            selected = (source, group) == (request.public or study_context.DEMO)
            mark = ' aria-current="true"' if selected else ""
            fields = revision + hidden("selection", source + "|" + group if source else "")
            details = f'<small>{e(units["department"])}</small>' if units.get("department") else ""
            cards.append(form(request, ROOT + "/groups", fields +
                              f'<button class="rp-choice" type="submit"{mark}>{e(units["label"])}{details}</button>'))
        body += section_title(name) + '<div class="rp-choices">' + "".join(cards) + "</div>"
    return shell(request, "Выберите группу", body + '<p class="rp-links">' + link("/schedule/context", "Все настройки учебного контекста") + "</p>")


def room(request, db):
    _, focus, _ = dates(request, db, request.public)
    prefs = raspay.preference(db, request.user, request.public)
    fields = settings_fields(request, db, focus, prefs)
    first = request.query("first") == "1"
    if first:
        fields += hidden("first", "1")
    path = ROOT + "/preferences/room"
    choices = raspay.language_rooms(db, request.user, request.public)
    cards = []
    for name in choices:
        mark = ' aria-current="true"' if name == prefs.room else ""
        cards.append(form(request, path, fields + hidden("room", name)
                          + f'<button class="rp-choice" type="submit"{mark}>{e(name)}</button>'))
    body = subhead("Личная аудитория для иностранного языка", url(focus) if first else url(focus, "/settings"),
                   "Личная отметка для этой группы")
    body += '<div class="rp-choices">' + "".join(cards) + "</div>" if cards else (
        '<p class="rp-empty">В расписании этой группы нет подтверждённых вариантов аудитории для английского.</p>')
    body += form(request, path, fields + hidden("room", "") + button("Выберу потом"), style="rp-action")
    return shell(request, "Личная аудитория для иностранного языка", body)


def preview(prefs, kind=None):
    names = {"lecture": "Алгоритмы и структуры данных", "practice": "Английский язык",
             "sport": "Физкультура", "lab": "Физика", "research": "Исследовательский семинар"}
    body = ""
    for key in (kind,) if kind else ("lecture", "practice"):
        label = raspay.PALETTES[key][0]
        body += (f'<div class="rp-preview-line"><time>09:50</time><div><span class="rp-kind">'
                 f'{dot(prefs.color(key))}{e(label)}</span><strong>{names[key]}</strong></div></div>')
    return '<div class="rp-preview"><span class="rp-kicker">Предпросмотр</span>' + body + "</div>"


def color_screen(request, db, kind=None):
    _, focus, _ = dates(request, db, request.public)
    prefs = raspay.preference(db, request.user, request.public)
    fields = settings_fields(request, db, focus, prefs)
    path = ROOT + "/preferences/color"
    if kind:
        if kind not in raspay.PALETTES:
            raise DomainError(404, "Тип занятия не найден.")
        label = raspay.PALETTES[kind][0]
        value = prefs.color(kind)
        picks = (f'<label>Палитра<input data-rp-picker type="color" value="{e(value)}" aria-label="Палитра"></label>'
                 + input_field("hex", "Код цвета", value, 'data-rp-hex pattern="#[0-9A-Fa-f]{6}" maxlength="7" required'))
        body = subhead("Свой цвет", url(focus, "/colors"), label) + preview(prefs, kind)
        body += form(request, path, fields + hidden("kind", kind) + picks + button("Применить"), style="rp-color-form")
        return shell(request, "Свой цвет", body)
    body = subhead("Цвета занятий", url(focus, "/settings")) + preview(prefs)
    for key, (label, *palette) in raspay.PALETTES.items():
        controls = []
        for color in palette:
            bg = (f'<svg viewBox="0 0 100 44" preserveAspectRatio="none" aria-hidden="true">'
                  f'<rect width="100" height="44" fill="{e(color)}"/></svg>')
            on = str(color == prefs.color(key)).lower()
            control = f'<button class="rp-swatch" type="submit" aria-pressed="{on}" aria-label="{e(label + " " + color)}">{bg}</button>'
            controls.append(form(request, path, fields + hidden("kind", key) + hidden("hex", color) + control))
        controls.append(f'<a class="rp-swatch rp-add-color" href="{e(url(focus, "/colors/" + key))}" '
                        f'aria-label="{e("Свой цвет · " + label)}">+</a>')
        body += f'<div class="rp-palettes"><h2>{dot(prefs.color(key))} {e(label)}</h2><div class="rp-swatches">' + "".join(controls) + "</div></div>"
    body += form(request, ROOT + "/preferences/reset-colors", fields + button("Вернуть стандартные"), style="rp-action")
    return shell(request, "Цвета занятий", body)
