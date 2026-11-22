
from datetime import datetime, time, timedelta
import json
from urllib.parse import urlencode
from zoneinfo import ZoneInfo

from . import activity_extensions as activities_ext, appearance
from . import reservations as res
from . import retakes, workspace
from .appearance import count_label
from .clock import now
from .domain import DomainError, role, timestamp
from .schedule import local_value
from .ui import (WEEKDAYS, back, button, day_label, e, empty, form, hidden, input_field, link, local_label, page,
                 panel, select_field)
from .study_views import date_time


def time_range(row, zone):
    begin, end = (local_value(row[key], zone) for key in ("starts_at", "ends_at"))
    if begin.date() == end.date():
        return f'{day_label(begin.date())} · {begin:%H:%M}–{end:%H:%M}'
    return f'{local_label(row["starts_at"], zone)} — {local_label(row["ends_at"], zone)}'


def retake_admission(debt):
    if debt["closed_at"]:
        return "Задолженность закрыта", "Посмотри результат в истории записей и результатов."
    if not debt["admitted"]:
        return ("Ожидает допуска", "Сотрудник твоей группы проверит условия допуска. "
                "Решение придёт в уведомлениях. Запись откроется после допуска.")
    return "Допуск подтверждён", "Выбери время с доступной записью и подтверди его в карточке окна."


def retake_badge(debt, label):
    tone = "good" if debt["closed_at"] or debt["admitted"] else "attention"
    return f'<p><strong class="tag" data-tone="{tone}">{e(label)}</strong></p>'


def retake_availability(db, user, debt, window, taken):
    if debt["closed_at"] or not debt["admitted"]:
        return False, retake_admission(debt)[0]
    if any(row["student_id"] == user["id"] for row in taken):
        return False, "У тебя уже есть запись на это окно."
    if window["closed"] or window["starts_at"] <= timestamp():
        return False, "Окно закрыто. Выбери другое время."
    if len(taken) >= window["capacity"]:
        return False, "Свободных мест нет. Выбери другое окно."
    try:
        retakes.check_new_attempt(db, user, window["bookable_id"], window["starts_at"], window["ends_at"])
    except DomainError as error:
        if error.status != 409:
            raise
        return False, error.message
    return True, "Можно записаться."


def catalog_page(request, db, activities=False):
    user = request.user
    workspace.scope(user)
    if activities and activities_ext.enabled():
        return activity_catalog(request, db)
    if not activities:
        return booking_catalog(request, db)
    category = request.query("category")
    all_rows = res.offers(db, user, "activity")
    choices = sorted({row["category"] for row in all_rows})
    rows = [row for row in all_rows if not category or row["category"] == category]
    filters = select_field("category", "Категория", [("", "Все категории"), *((x, x) for x in choices)], category)
    controls = f'<form method="get" action="/activities" class="inline">{filters}{button("Показать")}</form>'
    links = link("/bookings/mine?kind=activity", "Мои мероприятия")
    items = ""
    for row in rows:
        location = " · ".join(filter(None, (row["category"], row["place"], row["host"])))
        items += f'<li><div>{link(f"/bookables/{row["id"]}", row["title"])}<p class="meta">{e(location)}</p></div></li>'
    table = f'<ul class="rows">{items}</ul>' if items else empty("Подходящих предложений пока нет.")
    return page(request, "Афиша", controls + panel("Демонстрационные предложения", table, f"<div>{links}</div>"))


def booking_catalog(request, db):
    from . import room_occupancy
    from .views import directory_search_field, get_date

    user = request.user
    zone = ZoneInfo(user["timezone"])
    kinds = {"": "Все", **{key: label for key, label in res.KINDS.items() if key != "activity"}}
    kind = request.query("kind")
    kind = kind if kind in kinds else ""
    category, query = request.query("category"), request.query("q").strip()[:100]
    terms = query.casefold().replace("ё", "е").split()
    offers = [row for row in res.offers(db, user, kind) if row["kind"] in kinds]
    categories = sorted({row["category"] for row in offers})
    category = category if category in categories else ""
    tabs = ""
    summaries = {"room": "Переговорные и комнаты", "consultation": "Запись в деканат", "sport": "Занятия и секции"}
    for key, summary in summaries.items():
        target = "/bookings?" + urlencode({"kind": key, "q": query})
        selected = ' aria-current="page"' if key == kind else ""
        glyph = {"room": "building", "consultation": "consultation", "sport": "sport"}[key]
        tabs += (f'<a href="{e(target)}"{selected} data-kind="{key}">{appearance.icon(glyph)}'
                 f'<span><strong>{e(kinds[key])}</strong><small>{summary}</small></span>{appearance.icon("chevron-right")}</a>')
    tabs += (f'<a href="/activities" data-kind="activity">{appearance.icon("calendar")}<span><strong>Афиша</strong>'
             f'<small>События кампуса</small></span>{appearance.icon("chevron-right")}</a>')
    search = hidden("kind", kind) + directory_search_field("Найти место или занятие", query)
    category_picker = select_field("category", "Категория", [("", "Все категории"), *((item, item) for item in categories)], category)
    filters = (f'<details class="dean-booking-filters"{" open" if category else ""}><summary>Фильтры</summary>'
               f'{category_picker}{button("Показать")}</details>') if categories else ""
    today = now().astimezone(zone).date()
    day = max(today, get_date(request.query("date"), today)) if kind == "room" else today
    if kind == "room":
        search += hidden("date", day.isoformat())
    controls = (f'<form method="get" action="/bookings" class="dean-booking-search">{search}{filters}</form>'
                f'<h2>Виды бронирования</h2><nav class="dean-booking-types" aria-label="Вид записи">{tabs}</nav>')
    if room_occupancy.available(user):
        controls += ('<div class="dean-booking-occupancy">'
                     + link(room_occupancy.PATH, "Тепловая карта загруженности", "dean-secondary-action")
                     + '</div>')
    matches = []
    for row in offers:
        searchable = " ".join(row[key] for key in ("title", "place", "host", "category")).casefold().replace("ё", "е")
        if (not category or row["category"] == category) and all(term in searchable for term in terms):
            matches.append(row)
    action = link("/bookings/mine", "Мои записи", "dean-secondary-link")
    if kind == "room":
        agenda = room_agenda(db, user, matches, day, today, zone, category, query)
        return page(request, "Бронирования", controls + agenda, section="bookings",
                    breadcrumb=back("/all-services", "Сервисы"), heading_action=action)
    cards = []
    for row in matches:
        target = f'/bookables/{row["id"]}'
        windows = [window for window in res.slots(db, user, row["id"]) if not window["closed"]]
        timing = "Ближайших окон пока нет"
        for window in windows:
            taken = res.occupied(db, window)
            if row["kind"] == "room":
                interval = res.suggested_room_interval(window, taken)
                if not interval:
                    continue
                begin = interval[0].astimezone(zone)
            else:
                if window["starts_at"] <= timestamp() or len(taken) >= window["capacity"]:
                    continue
                begin = local_value(window["starts_at"], user["timezone"])
            timing = f'Ближайшее время · {day_label(begin.date())}, {begin:%H:%M}'
            break
        place = " · ".join(filter(None, (row["category"], row["place"])))
        glyph = {"room": "building", "consultation": "consultation", "sport": "sport"}[row["kind"]]
        cards.append(f'<a class="dean-booking-row" href="{e(target)}" data-kind="{e(row["kind"])}">'
                     f'<span class="dean-booking-mark">{appearance.icon(glyph)}</span>'
                     f'<span><strong>{e(row["title"])}</strong><small>{e(place)}</small></span>'
                     f'<small class="dean-booking-time">{e(timing)}</small></a>')
    content = f'<div class="dean-booking-grid">{"".join(cards)}</div>'
    if not cards:
        content = empty("По этим условиям ничего не найдено.") + link("/bookings", "Показать все варианты")
    reset = link("/bookings", "Все виды") if kind else ""
    listing = f'<section class="dean-booking-listing"><h2>{e(kinds[kind] if kind else "Доступные варианты")}</h2>{reset}{content}</section>'
    return page(request, "Бронирования", controls + listing, section="bookings", breadcrumb=back("/all-services", "Сервисы"),
                heading_action=action)


def room_agenda(db, user, offers, day, today, zone, category, query):
    base = {"kind": "room", "category": category, "q": query}
    path = "/bookings?"
    previous = link(path + urlencode(base | {"date": (day - timedelta(days=1)).isoformat()}),
                    "Предыдущий день") if day > today else ""
    following = link(path + urlencode(base | {"date": (day + timedelta(days=1)).isoformat()}), "Следующий день")
    filters = "".join(hidden(key, val) for key, val in base.items())
    filters += input_field("date", "Дата бронирования", day.isoformat(), f'min="{today.isoformat()}" required', "date")
    filters = f'<form method="get" action="/bookings">{filters}{button("Показать")}</form>'
    caption = f'{WEEKDAYS[day.weekday()]}, {day_label(day)}'
    navigation = f'<nav aria-label="Дни бронирования">{previous}{following}</nav>'
    tools = f'<div class="dean-room-day">{filters}{navigation}</div><p class="dean-room-date">{e(caption)}</p>'
    if not offers:
        return panel("Помещения на выбранный день", tools + empty("По этим условиям помещений нет."))
    midnight = datetime.combine(day, time.min, zone)
    next_day = datetime.combine(day + timedelta(days=1), time.min, zone)
    schedule = res.room_timetable(db, user, (offer["id"] for offer in offers),
                                  timestamp(midnight), timestamp(next_day))
    timeline = [(offer, schedule[offer["id"]]) for offer in offers]
    first = 8
    last = 21
    for _, windows in timeline:
        for window, _ in windows:
            begin, end = (local_value(window[key], zone.key) for key in ("starts_at", "ends_at"))
            first = min(first, begin.hour if begin.date() == day else 0)
            last = max(last, min(24, end.hour + bool(end.minute or end.second)) if end.date() == day else 24)
    headers = "".join(f'<th scope="col"><time>{hour:02}:00</time></th>' for hour in range(first, last))
    rows = []
    mobile_rooms = []
    for offer, windows in timeline:
        intervals = [room_interval(user, offer, windows, midnight + timedelta(hours=hour),
                                   midnight + timedelta(hours=hour + 1)) for hour in range(first, last)]
        cells = "".join(room_cell(*entry) for entry in intervals)
        detail = link(f'/bookables/{offer["id"]}', offer["title"])
        rows.append(f'<tr><th scope="row">{detail}<small>{e(offer["place"])}</small></th>{cells}</tr>')
        times = {entry[2]: entry for entry in intervals if entry[2]}
        choices = "".join(
            f'<li data-state="{state}"><a href="{e(url)}" aria-label="{e(label)}">{e(message)}</a></li>'
            for state, message, url, label in times.values()
        )
        availability = f'<ul>{choices}</ul>' if choices else empty("Свободного времени на этот день нет.")
        mobile_rooms.append(f'<section><h3>{detail}</h3><p class="meta">{e(offer["place"])}</p>{availability}</section>')
    legend = ('<ul class="dean-room-legend"><li data-state="open">Свободно</li><li data-state="own">Твоя бронь</li>'
              '<li data-state="busy">Занято</li><li data-state="closed">Недоступно</li></ul>')
    grid = (f'<div class="dean-room-scroll" role="region" aria-label="Свободное время по помещениям" tabindex="0">'
            f'<table class="dean-room-table"><caption>Расписание помещений · {e(caption)}</caption>'
            f'<thead><tr><th scope="col">Помещение</th>{headers}</tr></thead><tbody>{"".join(rows)}</tbody></table></div>')
    mobile = '<div class="dean-room-mobile">' + "".join(mobile_rooms) + "</div>"
    return panel("Помещения на выбранный день", tools + legend + grid + mobile, style="dean-room-agenda")


def room_interval(user, offer, windows, start, end):
    start_stamp, end_stamp = timestamp(start), timestamp(end)
    own, available = None, None
    state = "closed"
    for window, reservations in windows:
        if window["starts_at"] >= end_stamp or window["ends_at"] <= start_stamp:
            continue
        taken = [row for row in reservations if row["starts_at"] < end_stamp and row["ends_at"] > start_stamp]
        for row in taken:
            if row["student_id"] == user["id"]:
                own = row
                break
        if taken:
            state = "busy"
        if window["closed"] or window["ends_at"] <= timestamp():
            continue
        if proposal := res.suggested_room_interval(window, taken, start=start_stamp, end=end_stamp):
            url = f'/slots/{window["id"]}?' + urlencode({
                "starts": date_time(timestamp(proposal[0]), user["timezone"]),
                "ends": date_time(timestamp(proposal[1]), user["timezone"]),
            })
            available = proposal, url
    if own:
        state, message, url = "own", "Твоя бронь", f'/bookings/{own["id"]}'
    elif available:
        proposal, url = available
        local = tuple(moment.astimezone(start.tzinfo) for moment in proposal)
        state, message = "open", f'{local[0]:%H:%M}–{local[1]:%H:%M}'
    else:
        message = "Занято" if state == "busy" else "—"
        explicit = message if state == "busy" else "Недоступно"
        return state, message, "", explicit
    caption = "Свободно " + message if state == "open" else message
    label = f'{offer["title"]}, {day_label(start.date())}, {caption}'
    if state == "own":
        label += f', {time_range(own, user["timezone"])}'
    return state, message, url, label


def room_cell(state, message, url, label):
    if not url:
        return f'<td data-state="{state}" aria-label="{e(label)}">{e(message)}</td>'
    return f'<td data-state="{state}"><a href="{e(url)}" aria-label="{e(label)}">{e(message)}</a></td>'


def activity_catalog(request, db):
    user = request.user
    query = request.query("q").strip()[:100]
    category = request.query("category")
    period = request.query("period", "upcoming")
    periods = [("upcoming", "Предстоящие"), ("week", "Ближайшие 7 дней"), ("all", "Все даты")]
    if period not in dict(periods):
        period = "upcoming"
    free_only = request.query("free") == "1"
    slots = activities_ext.next_slots(db, user, timestamp(now() + timedelta(days=7)) if period == "week" else "")
    offers = res.offers(db, user, "activity")
    categories = sorted({row["category"] for row in offers})
    fields = input_field("q", "Поиск", query, 'maxlength="100" placeholder="Название, место, организатор"')
    fields += select_field("category", "Категория", [("", "Все категории"), *((x, x) for x in categories)], category)
    fields += select_field("period", "Когда", periods, period)
    fields += select_field("free", "Запись", [("", "Все"), ("1", "Есть места")], "1" if free_only else "")
    filters = '<form method="get" action="/activities" class="inline">' + fields + button("Найти") + "</form>"
    matches = []
    words = query.casefold().split()
    for offer in offers:
        if category and offer["category"] != category:
            continue
        searchable = " ".join(offer[name] for name in ("title", "category", "place", "host", "rules")).casefold()
        if not all(word in searchable for word in words):
            continue
        windows = slots.get(offer["id"], [])
        if free_only:
            windows = [window for window in windows if window["free"] > 0]
        if not windows and (free_only or period != "all"):
            continue
        first = windows[0] if windows else None
        matches.append((first["starts_at"] if first else "9999", offer, first))
    items = []
    for _, offer, first in sorted(matches, key=lambda item: (item[0], item[1]["title"])):
        when = local_label(first["starts_at"], user["timezone"]) if first else "Нет предстоящих дат"
        seats = ""
        if first:
            seats = ("Свободно " + count_label(first["free"], "место", "места", "мест")
                     if first["free"] else "Мест нет")
        category = offer["category"]
        pictogram = {"культура": "🎭", "спорт": "🏅"}.get(category.casefold(), "")
        mark = f'<span aria-hidden="true">{pictogram} </span>' if pictogram else ""
        details = " · ".join(filter(None, (offer["place"], offer["host"])))
        capacity = f'<span class="tag" data-tone="{"good" if first["free"] else "neutral"}">{e(seats)}</span>' if first else ""
        items.append(f'<li><span class="dean-activity-category">{mark}{e(category)}</span>'
                     f'{link(f"/bookables/{offer["id"]}", offer["title"])}'
                     f'<div class="dean-activity-time"><span>{e(when)}</span>{capacity}</div>'
                     f'<p class="meta">{e(details)}</p></li>')
    table = '<ul class="dean-activity-list">' + "".join(items) + "</ul>" if items else empty(
        "По этим условиям мероприятий нет. Попробуй другую дату или категорию.")
    actions = link("/bookings/mine?kind=activity", "Мои мероприятия")
    if query or category or free_only or period != "upcoming":
        actions += link("/activities", "Сбросить фильтры")
    grouped_actions = f'<div class="switcher">{actions}</div>'
    return page(request, "Афиша", filters + panel("Мероприятия", table, grouped_actions), section="activities")


def bookable_detail(request, db, bid):
    user = request.user
    row = res.offer(db, user, bid)
    activity = row["kind"] == "activity" and activities_ext.enabled()
    body = f'<p>{e(row["place"])}</p><p>{e(row["rules"])}</p>'
    if row["equipment"]:
        body += f'<p>Оборудование: {e(row["equipment"])}</p>'
    if row["kind"] == "room":
        body += f'<p>{link("/services/room-repair?room=" + bid, "Сообщить о неисправности")}</p>'
    if row["host"]:
        body += f'<p>{"Организатор" if activity else "Ведущий"}: {e(row["host"])}</p>'
    if activity and (target := activities_ext.map_url(db, bid)):
        body += f'<p>{link(target, "Маршрут до аудитории")}</p>'
    result = panel("О мероприятии" if activity else "Условия", body)
    debt = retakes.matching_debt(db, user, bid) if row["kind"] == "retake" else None
    if debt:
        admission, next_step = retake_admission(debt)
        detail = "/retakes/" + debt["id"]
        target = link(detail + "#retake-history", "Посмотреть результат") if debt["closed_at"] else link(detail, "К задолженности")
        admission_panel = panel("Допуск", retake_badge(debt, admission)
                                + f"<p>{e(debt['conditions'])}</p><p>{e(next_step)}</p>" + target)
        result = admission_panel + result
    zone = user["timezone"]
    groups = {}
    for window in res.slots(db, user, bid):
        taken = res.occupied(db, window)
        own = any(item["student_id"] == user["id"] for item in taken)
        state = "closed"
        if debt:
            available, label = retake_availability(db, user, debt, window, taken)
            if available:
                state = "open"
        elif window["closed"] or (window["kind"] != "room" and window["starts_at"] <= timestamp()):
            label = "Запись закрыта"
        elif window["kind"] == "room":
            if res.suggested_room_interval(window, taken):
                state, label = "open", "Есть свободное время"
            else:
                state, label = "busy", "Свободного времени нет"
        else:
            remaining = max(0, window["capacity"] - len(taken))
            state = "open" if remaining else "busy"
            label = f'Свободных мест: {remaining}' if remaining else "Свободных мест нет"
        if own:
            state, label = "own", "Ты уже записан"
        begin, end = (local_value(window[key], zone) for key in ("starts_at", "ends_at"))
        clock = f'{begin:%H:%M}–{end:%H:%M}' if begin.date() == end.date() else f'{begin:%H:%M}–{end:%H:%M}, {day_label(end.date())}'
        groups.setdefault(begin.date(), []).append(
            f'<li><a class="dean-slot-card" data-state="{state}" href="/slots/{e(window["id"])}">'
            f'<span><strong>{e(clock)}</strong><small>{e(label)}</small></span>{appearance.icon("chevron-right")}</a></li>')
    items = "".join(f'<div class="dean-slot-group"><h3>{WEEKDAYS[day.weekday()]}, {day_label(day)}</h3>'
                    f'<ul>{"".join(cards)}</ul></div>' for day, cards in groups.items())
    timetable = link("/bookings?kind=room", "Сетка помещений", "dean-secondary-link") if row["kind"] == "room" else ""
    times = panel("Даты и регистрация" if activity else "Доступное время",
                  items or empty("Предстоящих окон пока нет."), timetable, style="dean-slot-picker")
    result = result + times if activity or debt else times + result
    path = {"activity": "/activities", "retake": "/retakes", "room": "/bookings?kind=room"}.get(row["kind"], "/bookings")
    return page(request, row["title"], back(path, "К списку") + result,
                " · ".join(filter(None, (row["category"], row["place"]))), section="booking-detail")


def requested_room_interval(request, row, taken):
    start, end = request.query("starts"), request.query("ends")
    if not (start and end):
        return None
    try:
        begin, finish = res.reservation_times(request.user, row, start, end)
    except DomainError:
        return None
    if (timestamp() < begin and row["starts_at"] <= begin < finish <= row["ends_at"]
            and sum(item["starts_at"] < finish and item["ends_at"] > begin for item in taken) < row["capacity"]):
        return datetime.fromisoformat(begin), datetime.fromisoformat(finish)
    return None


def room_occupancy(window, reservations):
    start = datetime.fromisoformat(window["starts_at"])
    stop = datetime.fromisoformat(window["ends_at"])
    length = (stop - start).total_seconds()
    if length <= 0:
        return ""
    busy = []
    for reservation in reservations:
        lower = max(start, datetime.fromisoformat(reservation["starts_at"]))
        upper = min(stop, datetime.fromisoformat(reservation["ends_at"]))
        if lower < upper:
            offset = 1000 * (lower - start).total_seconds() / length
            duration = 1000 * (upper - lower).total_seconds() / length
            busy.append(f'<rect class="dean-occupancy-busy" x="{offset:.2f}" width="{duration:.2f}" height="10"/>')
    return ('<svg class="dean-occupancy" viewBox="0 0 1000 10" preserveAspectRatio="none" aria-hidden="true">'
            f'<rect width="1000" height="10"/>{"".join(busy)}</svg>'
            '<p class="dean-occupancy-key" aria-hidden="true"><span>Свободно</span><span>Занято</span></p>')


def room_selection(user, row, proposal, lessons, bookings, submitted):
    zone = user["timezone"]
    limits = {key: date_time(row[key], zone) for key in ("starts_at", "ends_at")}
    values = submitted if submitted is not None else {
        key: date_time(timestamp(value), zone) for key, value in zip(("starts", "ends"), proposal)
    }
    options = f'min="{limits["starts_at"]}" max="{limits["ends_at"]}" required'
    inputs = input_field("starts", "Начало записи", values["starts"], options, "datetime-local")
    inputs += input_field("ends", "Окончание записи", values["ends"], options, "datetime-local")
    data = [{"start": local_value(item.start, zone).replace(tzinfo=None).isoformat(),
             "end": local_value(item.end, zone).replace(tzinfo=None).isoformat(), "title": item.title}
            for item in lessons]
    booking_data = [{"start": local_value(item["starts_at"], zone).replace(tzinfo=None).isoformat(),
                     "end": local_value(item["ends_at"], zone).replace(tzinfo=None).isoformat(),
                     "title": item["title"], "sameSlot": item["slot_id"] == row["id"]} for item in bookings]
    conflicts = []
    own_conflicts = []
    try:
        start, end = res.reservation_times(user, row, values["starts"], values["ends"])
    except DomainError:
        pass
    else:
        if row["starts_at"] <= start < end <= row["ends_at"]:
            conflicts = [item.title for item in lessons if item.start < end and item.end > start]
            own_conflicts = [item["title"] for item in bookings if item["starts_at"] < end and item["ends_at"] > start
                             and (item["slot_id"], item["starts_at"], item["ends_at"]) != (row["id"], start, end)]
    state = "" if conflicts else " hidden"
    checked = ' checked' if conflicts and submitted and submitted["ack"] else ""
    warning = "Интервал пересекается с учебной парой."
    if conflicts:
        warning += " " + ", ".join(conflicts) + "."
    existing = "Интервал пересекается с твоей записью."
    if own_conflicts:
        existing += " " + ", ".join(dict.fromkeys(own_conflicts)) + ". Выбери другое время."
    existing_state = "" if own_conflicts else " hidden"
    return (f'<div data-room-lessons="{e(json.dumps(data, ensure_ascii=False))}" '
            f'data-room-bookings="{e(json.dumps(booking_data, ensure_ascii=False))}">'
            f'<div class="dean-slot-inputs">{inputs}</div>'
            f'<p class="change" role="status" data-room-booking-conflict{existing_state}>{e(existing)}</p>'
            f'<p class="change" role="status" data-room-conflict{state}>{e(warning)}</p>'
            f'<label class="check" data-room-ack{state}><input type="checkbox" name="ack" value="1"{checked}>'
            'Подтверждаю запись при пересечении с парой</label></div>')


def slot_detail(request, db, sid, *, submitted=None, error=""):
    user = request.user
    zone = user["timezone"]
    row = res.slot(db, user, sid)
    taken = res.occupied(db, row)
    free = row["capacity"] - len(taken)
    debt = retakes.matching_debt(db, user, row["bookable_id"]) if row["kind"] == "retake" else None
    retake_open, retake_message = retake_availability(db, user, debt, row, taken) if debt else (False, "")
    start, stop = (local_value(row[name], zone) for name in ("starts_at", "ends_at"))
    interval = f"{day_label(start.date())} · {start:%H:%M}–{stop:%H:%M}" if start.date() == stop.date() else time_range(row, zone)
    text = f'<p>{e(interval)}</p><p>{e(row["place"])}</p>'
    if row["kind"] == "room":
        text += room_occupancy(row, taken)
        intervals = "".join(f"<li>{e(time_range(item, zone))}</li>" for item in taken)
        text += f"<p>Занятые интервалы</p><ul>{intervals}</ul>" if taken else empty("В этом окне ещё нет броней.")
    elif not debt or retake_open:
        endings = ("свободное место", "свободных места", "свободных мест")
        ending = 0 if free % 10 == 1 else 1 if 2 <= free % 10 <= 4 else 2
        if 11 <= free % 100 <= 14:
            ending = 2
        text += f"<p>{free} {endings[ending]}.</p>" if free > 0 else "<p>Свободных мест нет.</p>"
    room = row["kind"] == "room"
    lessons = res.overlapping_lessons(db, user, row["starts_at"], row["ends_at"])
    conflicts = [item.title for item in lessons] if not room else []
    bookings = res.bookings(db, user, active_only=True, start=row["starts_at"], end=row["ends_at"])
    own = [x for x in taken if x["student_id"] == user["id"]]
    proposal = None
    if room:
        proposal = requested_room_interval(request, row, taken) or res.suggested_room_interval(row, taken)
    open_now = not row["closed"] and (row["ends_at"] if row["kind"] == "room" else row["starts_at"]) > timestamp()
    if debt:
        open_now = retake_open
        admission, next_step = retake_admission(debt)
        detail = "/retakes/" + debt["id"]
        details = retake_badge(debt, admission)
        if debt["closed_at"]:
            details += f"<p>{e(next_step)}</p>" + link(detail + "#retake-history", "Посмотреть результат")
        elif not debt["admitted"]:
            details += f"<p>{e(debt['conditions'])}</p><p>{e(next_step)}</p>"
            details += link(detail, "Сверить условия допуска")
        else:
            details += f"<p>{e(retake_message)}</p>"
            details += link(detail + "#retake-slots", "К другим окнам")
        result = panel("Допуск и запись", details + '<div class="ux-retake-time">' + text + "</div>")
    else:
        result = panel("Время и занятость", text)
    if own:
        own_links = " · ".join(link(f'/bookings/{x["id"]}', time_range(x, zone)) for x in own)
        result += panel("Твоя запись", f"<p>{own_links}</p>")
    if open_now and ((room and proposal is not None) or (not room and not own and free > 0)):
        fields = hidden("version", row["version"])
        if room:
            fields += room_selection(user, row, proposal, lessons, bookings, submitted)
        elif bookings:
            fields += f'<p class="change">Интервал пересекается с твоей записью. {e(bookings[0]["title"])}.</p>'
        if conflicts and row["kind"] != "retake":
            fields += f'<p class="change">Интервал пересекается с учебной парой. {e(", ".join(conflicts))}.</p>'
            fields += '<label class="check"><input type="checkbox" name="ack" value="1">Подтверждаю запись при совпадении с парой</label>'
        result += panel("Выбрать интервал" if room else "Записаться", form(request, f"/slots/{sid}/book",
                                           fields + button("Подтвердить запись"), command=True))
    elif row["kind"] == "room" and proposal is None:
        result += panel("Запись",empty("В этом окне нет свободного будущего интервала."))
    elif not open_now and not debt:
        result += panel("Запись", empty("Запись закрыта."))
    if row["kind"] in res.WAIT_KINDS and not own:
        queued = res.waiting(db, user, sid)
        wait_panel = ""
        if queued in {"waiting", "notified"}:
            label = "Ты в очереди." if queued == "waiting" else "Место могло освободиться. Проверь доступность."
            wait_panel += f"<p>{label}</p>" + form(request, f"/slots/{sid}/leave",
                                                   button("Выйти из списка ожидания"), command=True)
        if open_now and not free and queued != "waiting":
            wait_panel += form(request, f"/slots/{sid}/wait", button("Встать в список ожидания"), command=True)
        if open_now and not free and row["kind"] == "sport":
            state = res.wait_record(db, user, sid)
            if state and state["auto_until"] and state["auto_until"] > timestamp():
                label = local_label(state["auto_until"], zone)
                wait_panel += f'<p>Автозапись разрешена до {e(label)} при свободном интервале.</p>'
                wait_panel += form(request, f"/slots/{sid}/withdraw-auto", button("Отозвать согласие"), command=True)
            else:
                day = res.local_value(row["starts_at"], zone).date().isoformat()
                fields = input_field("from", "Действует на даты с", day, "required", "date")
                fields += input_field("to", "По", day, "required", "date")
                fields += input_field("until", "Согласие действует до", date_time(row["starts_at"], zone),
                                      "required", "datetime-local")
                fields += '<label class="check"><input type="checkbox" name="consent" value="1" required>Разрешаю автозапись на это окно при отсутствии пересечения</label>'
                wait_panel += form(request, f"/slots/{sid}/auto-wait", fields + button("Включить автозапись"), command=True)
        if wait_panel:
            result += panel("Список ожидания", wait_panel)
    feedback = f'<p class="change" role="alert" tabindex="-1" data-restore-focus>{e(error)}</p>' if error else ""
    return page(request, row["title"], feedback + result + back(f'/bookables/{row["bookable_id"]}', "К окнам"),
                section="booking-slot")


def my_bookings(request, db):
    user = request.user
    kind = request.query("kind")
    records = res.bookings(db, user, kind=kind)
    items = ""
    for record in records:
        status = "Подтверждена" if record["status"] == "reserved" else "Отменена"
        if activities_ext.enabled() and record["kind"] == "activity" and attendance(db, record["id"]):
            status = "Посещение подтверждено"
        items += f'<li><div>{link(f"/bookings/{record["id"]}", record["title"])}'
        items += f'<p class="meta">{e(time_range(record, user["timezone"]))} · {status}</p></div></li>'
    title = {"sport": "Мои тренировки", "activity": "Мои мероприятия"}.get(kind, "Мои записи")
    href = "/activities" if kind == "activity" else "/bookings"
    body = panel("Записи", f'<ul class="rows">{items}</ul>' if items else empty("Записей пока нет."))
    if kind == "sport":
        visits = len(res.sport_visits(db, user))
        body += panel("Подтверждённый прогресс", f'<p>Посещений {visits} · Демо-баллов {visits * 10}.</p>')
    return page(request, title, body + back(href, "К каталогу"))


def attendance(db, rid):
    return db.execute("SELECT 1 FROM reservation_attendance WHERE reservation_id=?", (rid,)).fetchone()


def booking_detail(request, db, rid):
    row = res.get_booking(db, request.user, rid)
    activity = row["kind"] == "activity" and activities_ext.enabled()
    attended = attendance(db, rid)
    upcoming = row["status"] == "reserved" and row["starts_at"] > timestamp()
    status = "Подтверждена" if row["status"] == "reserved" else "Отменена"
    details = f'<p>{status}</p><p>{e(time_range(row, request.user["timezone"]))}</p><p>{e(row["place"])}</p>'
    if upcoming and not (activity and attended):
        details += form(request, f"/bookings/{rid}/cancel", button("Отменить запись"), command=True)
    if attended:
        details += '<p><span class="tag">Посещение подтверждено</span></p>'
    if activity and row["status"] == "reserved":
        from . import calendar_tools

        zone = calendar_tools.period_for(request.user, request.public).timezone
        day = res.local_value(row["starts_at"], zone).date()
        url = "/schedule?" + urlencode({"date": day.isoformat(), "view": "day", "visibility": "all", "kind": "", "subject": ""})
        details += '<p>Регистрация добавлена в твой календарь.</p>'
        details += f'<p>{link(url, "Открыть в календаре")} · {link(f"/bookings/{rid}/calendar.ics", "Скачать .ics")}</p>'
        if target := activities_ext.map_url(db, row["bookable_id"]):
            details += f'<p>{link(target, "Маршрут до аудитории")}</p>'
        if upcoming and not attended:
            active = activities_ext.reminder_on(db, rid)
            details += "<p>Напоминание за 24 часа включено.</p>" if active else ""
            text = "Отключить напоминание" if active else "Напомнить за 24 часа"
            details += form(request, f"/bookings/{rid}/reminder",
                            hidden("active", "0" if active else "1") + button(text), command=True)
        achievement = db.execute("""
            SELECT p.id FROM portfolio_verification v JOIN portfolio_entry p ON p.id=v.entry_id
            WHERE v.reservation_id=? AND p.university_id=? AND p.student_id=?
        """, (rid, *workspace.scope(request.user))).fetchone() if attended else None
        if achievement:
            details += f'<p>{link("/portfolio/" + achievement["id"], "Открыть достижение в портфолио")}</p>'
    heading = panel("Регистрация на мероприятие" if activity else "Демонстрационная запись", details)
    if row["kind"] == "activity":
        from .event_ticket_views import menu

        heading += menu(request,db,row)
    target = "/bookings/mine?kind=activity" if activity else "/bookings/mine"
    return page(request, row["title"], heading + back(target, "К моим мероприятиям" if activity else "К моим записям"))


def staff_catalog(request, db):
    user = request.user
    role(user, "staff")
    body = ""
    for row in res.offers(db, user):
        if row["manager_id"] == user["id"]:
            body += f'<li><div>{link(f"/staff/bookables/{row["id"]}", row["title"])}<p class="meta">{e(row["place"])}</p></div></li>'
    return page(request, "Управление записью", panel("Мои ресурсы", f'<ul class="rows">{body}</ul>' if body
                                                   else empty("Назначенных ресурсов пока нет.")) + back("/staff", "К кабинету"))


def staff_offer(request, db, bid):
    user = request.user
    row = res.offer(db, user, bid, manage=True)
    items = ""
    for item in res.slots(db, user, bid, upcoming=False):
        label = "Закрыто" if item["closed"] else "Открыто"
        items += f'<li><div>{link(f"/staff/slots/{item["id"]}", time_range(item, user["timezone"]))}'
        items += f'<p class="meta">{label} · Записей: {len(res.occupied(db, item))}</p></div></li>'
    managed = activities_ext.enabled() and res.is_managed_activity(db, row, bid)
    action = link(f"/staff/manage/resources/{bid}", "Управлять мероприятием") if managed else link(
        f"/staff/bookables/{bid}/new", "Добавить окно")
    return page(request, row["title"], panel("Окна", f'<ul class="rows">{items}</ul>' if items else
                                             empty("Окон пока нет."), action)
                + back("/staff/bookings", "К ресурсам"))


def staff_slot(request, db, target, creating=False):
    user = request.user
    row = res.offer(db, user, target, manage=True) if creating else res.slot(db, user, target, manage=True)
    zone = user["timezone"]
    path = f"/staff/bookables/{target}/new" if creating else f"/staff/slots/{target}"
    bid = target if creating else row["bookable_id"]
    managed = activities_ext.enabled() and res.is_managed_activity(db, row, bid)
    back_to = f'/staff/bookables/{bid}'
    if managed:
        back_to = f"/staff/manage/resources/{bid}"
        if not creating:
            back_to += "?" + urlencode({"slot": target})
    values = {} if creating else row
    fields = input_field("starts", "Начало", date_time(values["starts_at"], zone) if values else "",
                          "required", "datetime-local")
    fields += input_field("ends", "Окончание", date_time(values["ends_at"], zone) if values else "",
                          "required", "datetime-local")
    fields += input_field("capacity", "Вместимость", values["capacity"] if values else "1",
                          'required min="1" max="1000"', "number")
    checked = " checked" if values and values["closed"] else ""
    fields += f'<label class="check"><input name="closed" type="checkbox" value="1"{checked}>Закрыть для новых записей</label>'
    if values:
        fields += hidden("version", values["version"])
    body = (panel("Управление мероприятием", link(back_to, "Открыть карточку сотрудника"))
            if managed else panel("Доступность", form(request, path, fields + button("Сохранить окно"), command=True)))
    if values:
        names = ""
        for person in res.participants(db, user, target):
            detail = f'<strong>{e(person["full_name"])}</strong><p class="meta">{e(time_range(person, zone))}</p>'
            if person["confirmed_at"]:
                detail += "<p>Посещение подтверждено.</p>"
                if row["kind"] == "activity":
                    fields = hidden("confirmed_at",person["confirmed_at"])
                    fields += input_field("reason","Причина исправления",options='required maxlength="400"')
                    detail += form(request, f'/staff/slots/{target}/attendance/{person["id"]}/correct',
                                   fields+button("Отозвать отметку"),command=True)
            elif row["kind"] in {"sport", "activity"} and person["ends_at"] <= timestamp():
                detail += form(request, f'/staff/slots/{target}/attendance/{person["id"]}',
                               button("Подтвердить посещение"), command=True)
            names += f'<li><div>{detail}</div></li>'
        body += panel("Подтверждённые участники", f'<ul class="rows">{names}</ul>' if names else
                      empty("Подтверждённых участников нет."))
    if values and row["kind"] == "activity":
        body += link(f"/staff/slots/{target}/tickets","Проверить билет")
    return page(request, row["title"], body + back(back_to, "К окнам"))
