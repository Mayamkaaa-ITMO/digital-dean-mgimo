"""Anonymous read-only directory of documented university sources."""

import json
from datetime import timedelta
from os import getenv
from urllib.parse import urlencode
from zoneinfo import ZoneInfo

from . import campus_demo, public_schedule as catalog, public_views, study_context
from .clock import now
from .domain import DomainError
from .schedule import local_value
from .ui import WEEKDAYS, back, button, day_label, e, empty, input_field, link, page, panel, select_field

PLACES = {
    "tusur-timetable": ("Томск", "Томский государственный университет систем управления и радиоэлектроники"),
    "spbpu-ruz": ("Санкт-Петербург", "Санкт-Петербургский политехнический университет Петра Великого"),
    "susu-timetable": ("Челябинск", "Южно-Уральский государственный университет"),
    "nspu-reference": ("Новосибирск", "Новосибирский государственный педагогический университет"),
    "guap-reference": ("Санкт-Петербург", "Санкт-Петербургский государственный университет аэрокосмического приборостроения"),
}
STATUSES = {
    "live": "Получено по сети",
    "snapshot": "Сохранённый снимок",
    "reference": "Справочная сетка",
    "link": "Официальная ссылка",
    "demo": "Демонстрационные занятия",
}


def group_link(source, group, monday=None, filters=None):
    params = {"source": source, "group": group, **(filters or {})}
    if monday:
        params["week"] = monday
    return "/universities/group?" + urlencode(params)


def weeks(db, source, group):
    return db.execute("""
        SELECT monday, observed_at, provenance FROM public_week
        WHERE source_id=? AND group_id=? AND revision>0 AND provenance IN ('snapshot','live')
        ORDER BY monday DESC
    """, (source, group)).fetchall()


def current_monday(zone):
    today = now().astimezone(ZoneInfo(zone)).date()
    return today - timedelta(days=today.weekday())


def nearest_week(publications, monday):
    def distance(row):
        start = study_context.date_value(row["monday"])
        return abs((start - monday).days), start < monday

    return min(publications, default=None, key=distance)


def week_label(day):
    end = day + timedelta(days=6)
    if day.month == end.month:
        return f"{day.day}–{day_label(end)}"
    return f"{day_label(day)} — {day_label(end)}"


def status(edition, group):
    if edition:
        return "live" if edition["provenance"] == "live" else "snapshot"
    return "reference" if group.get("pattern") else "link"


def clean(value):
    return value.casefold().replace("ё", "е")


def catalog_filters(request):
    return {key: value for key, value in (
        ("q", request.query("q").strip()[:120]),
        ("city", request.query("city")[:80]), ("status", request.query("status")[:15])
    ) if value}


def catalog_return(filters, source, group):
    query = "?" + urlencode(filters) if filters else ""
    return f"/universities{query}#group-{source}-{group.replace('/', '-')}"


def guest(request, db):
    body = '<p>Найди вуз и группу. Доступные снимки и справочные материалы можно посмотреть без входа.</p>'
    body += '<p>' + link("/universities", "Открыть каталог вузов", "rw-primary-link") + "</p>"
    body += '<p>' + link("/campus-map", "Карта кампуса") + "</p>"
    href, caption = ("/", "Вернуться в кабинет") if request.user else ("/login", "Войти в личный кабинет")
    body += '<p>' + link(href, caption) + "</p>"
    return page(request, "Расписание вузов", body, section="guest")


def index(request, db):
    chosen = catalog_filters(request)
    query, city, mode = (chosen.get(key, "") for key in ("q", "city", "status"))
    cities = sorted({"Москва", *(place[0] for place in PLACES.values())})
    if city not in {"", *cities} or mode not in {"", *STATUSES}:
        raise DomainError(422, "Выбери город и статус из списка.")
    fields = input_field("q", "Страна, город, вуз или группа", query, 'maxlength="120"')
    fields += select_field("city", "Город", [("", "Все города"), *((x, x) for x in cities)], city)
    fields += select_field("status", "Данные", [("", "Все статусы"), *STATUSES.items()], mode)
    body = '<form method="get" action="/universities" class="inline">' + fields + button("Найти") + "</form>"
    results = []
    terms = clean(query).split()
    demo_terms = clean(f"Россия Москва кампус {campus_demo.GROUP}")
    if city in {"", "Москва"} and mode in {"", "demo"} and all(term in demo_terms for term in terms):
        results.append(panel("Демонстрационный кампус", '<p class="meta">Москва · Демонстрационные занятия</p>'
                             + link(campus_demo.SCHEDULE, campus_demo.GROUP)))
    for source_id, source in catalog.SOURCES.items():
        town, full_name = PLACES[source_id]
        if city and city != town:
            continue
        rows = []
        monday = current_monday(source["zone"])
        for group_id, group in source["groups"].items():
            searchable = clean(" ".join(("Россия", town, source["name"], full_name, group["label"], group.get("department", ""))))
            if any(term not in searchable for term in terms):
                continue
            available = weeks(db, source_id, group_id)
            edition = nearest_week(available, monday)
            mark = status(edition, group)
            if mode and mark != mode:
                continue
            date = f' · данные за {week_label(study_context.date_value(edition["monday"]))}' if edition else ""
            anchor = f'group-{source_id}-{group_id.replace("/", "-")}'
            rows.append(f'<li id="{e(anchor)}"><div>{link(group_link(source_id, group_id, filters=chosen), group["label"])}'
                        f'<p class="meta">{e(STATUSES[mark] + date)}</p></div></li>')
        if rows:
            region = f"{town}, Россия" if getenv("DEAN_PRODUCT") == "1" else f"{full_name} · {town}, Россия"
            location = f'<p class="meta">{e(region)}</p>'
            results.append(panel(catalog.source_label(source), location + '<ul class="rows">' + "".join(rows) + "</ul>"))
    if chosen:
        body += f'<p>{link("/universities", "Сбросить фильтры")}</p>'
    body += (f'<div class="rw-directory-grid">{"".join(results)}</div>' if results else
             empty("Ничего не нашлось. Попробуй номер группы, название вуза или город."))
    return page(request, "Каталог вузов", body, "Группы и доступные материалы открытых источников", section="university-index")


def dated_rows(db, source, group_id, monday):
    rows = db.execute("""
        SELECT subject,kind,starts_at,ends_at,rooms_json FROM public_lesson
        WHERE source_id=? AND group_id=? AND monday=? AND cancelled=0
        ORDER BY starts_at,subject,id
    """, (source["source_id"], group_id, monday))
    items = []
    for row in rows:
        start = local_value(row["starts_at"], source["zone"])
        end = local_value(row["ends_at"], source["zone"])
        caption = f'{WEEKDAYS[start.weekday()]}, {day_label(start.date())} · {start:%H:%M}–{end:%H:%M}'
        details = " · ".join([row["kind"], *(label for label, _ in json.loads(row["rooms_json"]))])
        items.append(f'<li><div><strong>{e(row["subject"])}</strong><p>{e(caption)}</p><p class="meta">{e(details)}</p></div></li>')
    return '<ul class="rows">' + "".join(items) + "</ul>" if items else empty("В этой сохранённой неделе занятий нет.")


def group(request, db):
    from .access_paths import login_url
    source_id, group_id = request.query("source"), request.query("group")
    source = catalog.SOURCES.get(source_id)
    item = source["groups"].get(group_id) if source else None
    if item is None:
        raise DomainError(404, "Группа не найдена в каталоге.")
    publications = weeks(db, source_id, group_id)
    monday_today = current_monday(source["zone"])
    fallback = nearest_week(publications, monday_today)
    wanted = request.query("week")
    if wanted:
        wanted = study_context.date_value(wanted).isoformat()
    current = next((edition for edition in publications if edition["monday"] == wanted), None) if wanted else fallback
    official = source["origin"] + item["path"]
    town, full_name = PLACES[source_id]
    filters = catalog_filters(request)
    body = back(catalog_return(filters, source_id, group_id), "К каталогу вузов")
    return_to = login_url(group_link(source_id, group_id, current["monday"] if current else wanted))
    caption = "Войти в кабинет и вернуться к расписанию"
    if not request.user:
        body += '<p class="rw-only">' + link(return_to, caption, "rw-primary-link") + "</p>"
    region = f"{town}, Россия" if getenv("DEAN_PRODUCT") == "1" else f"{full_name} · {town}, Россия"
    facts = f'<p>{e(region)}</p>'
    if item.get("department"):
        facts += f'<p>{e(item["department"])}</p>'
    if item.get("program"):
        facts += f'<p>Программа · {e(item["program"])}</p>'
    else:
        facts += '<p class="meta">Связь группы с конкретной программой пока не подтверждена.</p>'
    facts += f'<p><strong>{e(STATUSES[status(current or fallback, item)])}</strong></p>'
    facts += f'<p>{link(official, "Официальная страница группы")}</p>'
    body += panel("О группе", facts)
    if publications:
        links = []
        for row in reversed(publications):
            monday = row["monday"]
            active = ' aria-current="page"' if current and current["monday"] == monday else ""
            start = study_context.date_value(monday)
            label = week_label(start)
            if start == monday_today:
                label += " · Текущая"
            elif start == monday_today + timedelta(days=7):
                label += " · Следующая"
            links.append(f'<a href="{e(group_link(source_id, group_id, monday, filters))}"{active}>{e(label)}</a>')
        body += f'<nav class="switcher" aria-label="Доступные недели">{" ".join(links)}</nav>'
    if current:
        day = study_context.date_value(current["monday"])
        date_text = f'Неделя с {day_label(day)} {day.year}'
        if not wanted and day != monday_today:
            body += empty(f"На текущую неделю данных нет. Открыта ближайшая доступная неделя · {week_label(day)}.")
        when = f'<p class="meta">Источник прочитан {e(current["observed_at"])} · {e(source["zone"])}</p>'
        body += panel(date_text, when + dated_rows(db, source, group_id, current["monday"]))
    elif item.get("pattern") and not wanted:
        body += public_views.pattern_panel((source_id, group_id), lambda key: "/universities/room?" + urlencode({
            "source": source_id, "group": group_id, "room": key,
        }))
    else:
        body += panel("Расписание", empty("Для этой даты пока нет подтверждённых данных."))
    if not request.user:
        body += '<p class="legacy-only">' + link(return_to, caption) + "</p>"
    return page(request, catalog.source_label(source) + " · " + item["label"], body, section="university-group")


def room(request, db):
    source_id, group_id, room_id = (request.query(key) for key in ("source", "group", "room"))
    source = catalog.SOURCES.get(source_id, {})
    group = source.get("groups", {}).get(group_id)
    card = source.get("room_cards", {}).get(room_id)
    if group is None or card is None or not any(row.get("room_ref") == room_id for row in group.get("pattern", ())):
        raise DomainError(404, "Помещение не найдено в справочнике этой группы.")
    content = f'<p>{e(card["detail"])}</p>' if card.get("detail") else empty("Обозначение из расписания группы.")
    content += link(card["url"], "Официальный источник")
    return page(request, card["label"], back(group_link(source_id, group_id), "К группе")
                + panel("Помещение", content))
