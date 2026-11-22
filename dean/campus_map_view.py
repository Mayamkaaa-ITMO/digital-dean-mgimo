from collections import Counter
import re
from urllib.parse import parse_qsl, urlencode

from . import calendar_tools, campus_demo, campus_map as maps, room_occupancy, study_context
from .domain import DomainError
from .auth import digest
from .campus_geometry import display_plans, drawing_path
from .appearance import count_label, icon
from .ui import e, hidden, link, page


GLYPHS = {
    "entrance": "M-6 7V-6H0M-1 1H8L4 -3M8 1L4 5",
    "stairs": "M-7 7H-3V2H2V-3H7V-8",
    "passage": "M-7 -3H7L3 -7M7 -3L3 1M7 4H-7L-3 0M-7 4L-3 8",
    "expand": "M-3 -7H-7V-3M3 -7H7V-3M7 3V7H3M-3 7H-7V3",
    "collapse": "M-7 -3H-3V-7M7 -3H3V-7M7 3H3V7M-7 3H-3V7",
    "pin": "M0 9S-7 3-7-2a7 7 0 0 1 14 0c0 5-7 11-7 11ZM2-2a2 2 0 1 0-4 0 2 2 0 0 0 4 0Z",
    "fit": "M-8-3V-8H-3M3-8H8V-3M8 3V8H3M-3 8H-8V3M-3-3H3V3H-3Z",
    "back": "M3-6-3 0l6 6",
    "close": "M-5-5 5 5M5-5-5 5",
    "check": "M-7 0-2 5 7-5",
    "food": "M-6-8v6q2 4 4 0v-6m-2 0V8M5 8V-8q-4 2-4 8h4",
    "grounds": "M-8-5-3-7 3-5 8-7V5L3 7-3 5-8 7ZM-3-7V5M3-5V7",
    "wardrobe": "M0-2q0-3 2-3a2 2 0 1 0-4 0M0-2-8 4v2H8V4Z",
    "auditorium": "M-8-6H8V4H-8ZM0 4V8m-4 0h8",
}
OFFICIAL_PLACES = (
    ("Столовые и кафе", "столовые питание кафе буфет", "https://mgimo.ru/territory/infrastructure/food/"),
    ("Библиотека", "библиотека читальный зал", "https://mgimo.ru/library/"),
)
CANTEEN_NAME = "Столовая «Трёшка»"
CANTEEN_ALIASES = ("столовая", "столовую", "столовой", "трешка", "треха", "трешку", "треху")
CAMPUS_SCHEME = "https://digital2022.mgimo.ru/local/templates/landing/i/scheme-01.png"
CANTEEN_GUIDE = "https://t.me/s/studsouzmgimo?after=6405#6412"
CANTEEN_STEPS = "Спустись по лестнице у 3154 и следуй указателям «Столовая»."
START_MAX_AGE = 8 * 60 * 60

# Building outlines follow the positions on the university's published overview.
# Bridges and courtyards are architectural context, independent of route geometry.
GROUNDS_OUTLINES = (
    ("a", "M257 321 320 305 340 309 371 298 373 307 344 317 352 377 276 395Z", 302, 349, "А"),
    ("b", "M338 218 351 214 349 211 423 190 427 206 435 204 445 241 430 245 443 292 430 298 "
           "430 305 376 321 361 261 342 267 333 236 339 234Z"
           "M362 229 419 213 439 279 382 295Z", 399, 258, "Б"),
    ("v", "M437 247 483 236 488 279 560 271 554 227 594 231 599 217 605 218 607 198 "
           "620 200 610 287 446 308 440 286Z", 535, 288, "В"),
    ("g", "M609 195 664 201 664 198 673 199 673 203 682 204 682 201 710 204 710 210 721 211 "
           "721 215 731 216 733 198 758 201 756 211 783 215 782 222 755 219 755 227 724 225 "
           "720 292 736 294 735 302 720 300 716 307 711 306 711 301 632 292 632 294 624 294 625 282 "
           "610 280 610 271 626 272 628 265 683 271 700 270 706 221 682 219 680 233 661 231 "
           "662 223 618 219 614 242 611 244Z", 665, 253, "Г"),
    ("n", "M736 277 876 288 874 299 880 300 883 273Q911 274 918 292Q929 331 886 351L878 325 "
           "872 325 870 334 807 329 806 331 761 327 733 309Z", 824, 306, "Новый корпус"),
    ("library", "M557 84 616 82 617 108 625 108 625 116 617 116 617 129 560 131Z", 589, 107, "Д"),
    ("pool", "M751 82 797 88 795 110 832 113 832 103 909 110 902 175 826 169 831 120 794 117 793 124 748 119Z",
     865, 141, "Спорткомплекс"),
    ("dorm", "M973 243 998 247 995 268 1012 284 996 301 979 286 958 293 948 274 970 263Z", 0, 0, ""),
)
CANTEEN_OUTLINE = "M781 209 793 210 793 215 800 215 801 209 840 212 840 204 879 208 876 248 838 244 838 253 795 249 797 229 790 228 789 235 778 233Z"

# The v3 trace puts several room numbers by the outline of their own room.
# These shifts follow the two adjacent rows on the same source plan.
LABEL_AREAS = {
    "v4": {"4102": 23, "4102а": 27},
    "g3": {"3122": 50, "3124": 2, "3126": 8, "3134": 39, "3157": 20},
}


def map_icon(kind):
    return (f'<svg class="campus-icon" viewBox="-12 -12 24 24" aria-hidden="true" '
            f'fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" '
            f'stroke-linejoin="round"><path d="{GLYPHS[kind]}"/></svg>')


def url(fragment="", **query):
    query = {key: value for key, value in query.items() if value not in ("", None)}
    return maps.PATH + ("?" + urlencode(query) if query else "") + ("#" + fragment if fragment else "")


def floor_url(data, key, *, fragment="campus-plan", **query):
    floor = data.floors[key]
    return url(fragment, building=floor.building, floor=floor.level, **query)


def grounds_url(way, *, place="", via="", floor=None, fragment="campus-plan"):
    level = {"building": floor.building, "floor": floor.level} if floor else {}
    return url(fragment, view="grounds", place=place, via=via, **level, **way)


def route_to(point, start, back=""):
    if point.selectable and point.key != start:
        return url("campus-plan", back=back, **{"from": start, "to": point.key})
    return ""


def point_link(data, point, way, *, context=None, fragment="campus-place"):
    """Use the same endpoint selection on the plan, in search and in the list."""
    if not point.selectable:
        return floor_url(data, point.floor, fragment=fragment, room=point.key, **way)
    mode = way.get("field")
    if mode == "from":
        origin = {"from": point.key, "remember_start": "1", "back": way.get("back")}
        if way.get("to"):
            return url("campus-plan", **origin, to=way["to"], step=1)
        context = context or {}
        target = way.get("target")
        if not target and (selected := data.places.get(context.get("room"))) and selected.selectable:
            target = selected.key
        if context.get("view") == "grounds":
            if not target and context.get("place") == "canteen":
                target = "canteen"
            return grounds_url({**origin, "target": target}, place=context.get("place"),
                               floor=data.floors[point.floor],
                               fragment="campus-place" if context.get("place") else "campus-query")
        return floor_url(data, point.floor, fragment="campus-query", target=target, **origin)

    browsing = dict(way)
    if mode == "to" and way.get("to"):
        return url("campus-plan", back=way.get("back"), step=1,
                   **{"from": way["from"], "to": point.key})
    if not browsing.get("to"):
        browsing.pop("target", None)
    return floor_url(data, point.floor, fragment=fragment, room=point.key, **browsing)


def edit_state(request, data):
    state = {}
    field = request.query("field")
    target = request.query("target")
    if field in {"from", "to"}:
        state["field"] = field
    if target == "canteen" or ((point := data.places.get(target)) and point.selectable):
        state["target"] = target
    return state


def return_path(request):
    path, _, context = request.query("back").partition("?")
    if path not in campus_demo.LESSON_RETURN_PATHS:
        if not request.user:
            return ""
        occupancy = path == room_occupancy.PATH and room_occupancy.available(request.user)
        student_detail = re.fullmatch(r"/(?:schedule(?:/public|/raspay/lesson)?|bookables)/[a-f0-9]{32}", path)
        staff_detail = request.user["role"] == "staff" and re.fullmatch(r"/staff/schedule/[a-f0-9]{32}", path)
        if not (occupancy or student_detail or staff_detail):
            return ""
    if not context or "#" in context:
        return path
    try:
        params = dict(parse_qsl(context, keep_blank_values=True, max_num_fields=5))
    except ValueError:
        return path
    if path.startswith("/schedule/raspay/lesson/"):
        try:
            focus = study_context.date_value(params.get("date"))
        except DomainError:
            return path
        query = urlencode({"date": focus.isoformat()})
    elif path.startswith("/schedule/"):
        query = calendar_tools.lesson_context(params.get)
    elif path == room_occupancy.PATH and params.get("period") in room_occupancy.PERIODS:
        query = urlencode({"period": params["period"]})
    else:
        query = ""
    return path + ("?" + query if query else "")


def back_link(path):
    if not path:
        return ""
    if path.startswith(room_occupancy.PATH):
        label = "К загруженности аудиторий"
    else:
        label = "К карточке мероприятия" if path.startswith("/bookables/") else "К занятию"
    return f'<p class="campus-return">{link(path, label, "campus-button")}</p>'


def mobile_bar(request, path):
    if not path:
        path = "/guest" if not request.user else "/staff" if request.user["role"] == "staff" else "/all-services"
    return ('<nav class="campus-mobile-bar" aria-label="Управление картой">'
            f'<a class="campus-button" href="{e(path)}" aria-label="Выйти из карты">{icon("left")}</a>'
            '<strong>Карта кампуса</strong>'
            f'<a class="campus-button" href="#campus-search" data-campus-search-focus '
            f'aria-controls="campus-search" aria-label="Поиск на карте">{icon("search")}</a>'
            '<a class="campus-button" href="#campus-details" data-campus-details '
            'aria-controls="campus-details">Детали</a></nav>')


def place_title(point):
    office = maps.OFFICE_NAMES.get((point.floor, point.label))
    if office:
        return f"Кабинет {point.label} · {office}"
    if point.kind in {"room", "location"} and point.label[:1].isdigit():
        kind = "Аудитория" if point.kind == "room" else "Помещение"
        return f"{kind} {point.label}"
    return point.label


def picker(data, floor, way):
    buildings = []
    for key, name in data.buildings.items():
        if key == floor.building:
            buildings.append(f'<li class="campus-building-active" aria-current="page">{e(name)}'
                             '<span class="campus-building-current">Выбран</span></li>')
        else:
            entry = next(code for code, level in data.floors.items() if level.building == key)
            buildings.append(f'<li>{link(floor_url(data, entry, **way), name)}</li>')
    levels = []
    available = sorted(((key, level) for key, level in data.floors.items() if level.building == floor.building),
                       key=lambda option: option[1].level)
    if len(available) == 1:
        level_picker = f'<p class="campus-level-static">{floor.level}-й этаж</p>'
    else:
        for key, level in available:
            if level == floor:
                levels.append(f'<span class="campus-level-current" aria-current="page" '
                              f'aria-label="{level.level}-й этаж">{level.level}</span>')
            else:
                href = floor_url(data, key, **way)
                levels.append(f'<a href="{e(href)}" aria-label="{level.level}-й этаж">{level.level}</a>')
        level_picker = (f'<nav class="campus-levels" aria-label="Этажи"><span class="campus-level-caption">Этаж</span>'
                        f'{"".join(levels)}</nav>')
    return (f'<div class="campus-filters"><div class="campus-filter-actions"><details class="campus-building">'
            f'<summary aria-label="Сменить корпус, сейчас {e(data.buildings[floor.building])}">'
            f'{icon("building")}<span>{e(data.buildings[floor.building])}</span></summary>'
            f'<ul><li class="campus-building-grounds">{link(grounds_url(way, floor=floor), "Карта территории")}</li>'
            f'{"".join(buildings)}</ul></details>'
            f'<a class="campus-button campus-map-search" href="#campus-search" aria-label="Поиск на карте" '
            f'data-campus-search-focus>{icon("search")}<span>Поиск</span></a></div>{level_picker}</div>')


def search_context(request):
    return {name: value for name in ("room", "step", "view", "place", "via")
            if (value := request.query(name))}


def search_panel(request, data, phrase, way, floor, start, *, navigating=False, grounds=False):
    context = search_context(request)
    state = {"building": floor.building, "floor": floor.level, **way, **context}
    mode = state.pop("field", "")
    origin = mode == "from"
    tabs = []
    target_key = way.get("to") or way.get("target")
    target = data.places.get(target_key)
    canteen = target_key == "canteen" or context.get("place") == "canteen"
    destination = CANTEEN_NAME if canteen else target.label if target else "Выбрать место"
    for field, title, value in (("from", "Откуда", start.label), ("to", "Куда", destination)):
        href = url("campus-query", **state, field=field)
        current = ' aria-current="page"' if (field == "from") == origin else ""
        tabs.append(f'<a href="{e(href)}"{current}><strong>{title}</strong><span>{e(value)}</span></a>')
    modes = f'<nav class="campus-search-modes" aria-label="Точки маршрута">{"".join(tabs)}</nav>'
    preserve = "".join(hidden(name, value) for name, value in state.items())
    if mode:
        preserve += hidden("field", mode)
    prompt = "Откуда идёшь?" if origin else "Найти другое место" if navigating else "Куда идти?"
    primary = "" if navigating else " campus-primary"
    search = (f'<form method="get" action="{maps.PATH}#campus-search" class="campus-search-form">'
              f'{preserve}<label for="campus-query">{prompt}</label><div class="campus-search-input">'
              f'{icon("search")}<input id="campus-query" type="search" name="q" value="{e(phrase)}" '
              'maxlength="80" autocomplete="off" placeholder="Номер или название">'
              f'<button type="submit" name="pick" value="1" class="campus-button{primary}">Найти</button></div></form>')
    results = ""
    if phrase:
        rows = []
        for result in search_matches(data, phrase, way, floor, context)[:15]:
            hint = result.get("hint", " · Подход по плану" if result["location_only"] else "")
            style = ' class="campus-result-canteen"' if result.get("place") == "canteen" else ""
            rows.append(f'<li><a href="{e(result["href"])}"{style}><span>{e(result["display_title"])}'
                        f'<small>{e(result["floor"] + hint)}</small></span></a></li>')
        results = (f'<ul class="campus-results campus-quick-results">{"".join(rows)}</ul>' if rows else
                   f'<p class="campus-empty" role="status">«{e(phrase)}» не нашлось на этой схеме. '
                   'Проверь номер аудитории или попробуй другое название.</p>')
    shortcuts = ""
    if not origin and not navigating and not grounds:
        shortcuts = (
            '<div class="campus-shortcuts" data-campus-shortcuts>'
            f'<a class="campus-shortcut campus-shortcut-food" href="{e(grounds_url(way, place="canteen", floor=floor))}">'
            f'{map_icon("food")}Столовая</a>'
            f'<a class="campus-shortcut" href="{e(grounds_url(way, floor=floor))}">{map_icon("grounds")}Территория</a></div>'
        )
    route = ""
    if not way.get("to"):
        if canteen:
            target = data.places[maps.CANTEEN_APPROACH]
        if target:
            if target.key == start.key:
                route = ('<p class="campus-small campus-search-help" role="status">'
                         'Точки совпадают. Выбери другое начало или назначение.</p>')
            elif href := route_to(target, start.key, way.get("back", "")):
                route = link(href, "Построить маршрут", "campus-button campus-primary campus-search-route")
    help_text = ('<p class="campus-small campus-search-help">Выбери точку на плане или воспользуйся поиском.</p>'
                 if mode else "")
    return (f'<section id="campus-search" class="campus-search" aria-label="Поиск на карте">{modes}{search}{help_text}'
            f'<div data-campus-quick-results hidden></div><div data-campus-search-submitted>{results}</div>'
            f'{route}{shortcuts}</section>')


def search_matches(data, phrase, way, floor=None, context=None):
    points = maps.find(data, phrase)
    results = [
        {"point": point.key, "title": point.label, "display_title": place_title(point), "floor": data.location(point.floor),
         "location_only": point.kind == "location", "href": point_link(data, point, way, context=context),
         "route": route_to(point, way.get("from", maps.ENTRY), way.get("back", ""))}
        for point in points
    ]
    words = maps.normalize(phrase).split()
    if words and (words == ["корпус", "ж"] or words == ["столовая", "трешка"] or all(
        any(term.startswith(word) for term in CANTEEN_ALIASES) for word in words
    )):
        target = dict(way)
        if target.get("field") == "to":
            target.pop("to", None)
            target.pop("progress", None)
            target["target"] = "canteen"
        results.insert(0, {"title": CANTEEN_NAME, "display_title": CANTEEN_NAME, "floor": "Корпус Ж · территория",
                           "place": "canteen", "location_only": False, "href": grounds_url(target, place="canteen", floor=floor, fragment="campus-place"),
                           "route": ""})
    if way.get("field") == "from":
        for row in results:
            point = data.places.get(row.get("point"))
            if not point or not point.selectable:
                row.update(unavailable_start=True, hint=" · Старт недоступен, посмотреть план")
                continue
            row.update(route="", hint=" · Выбрать старт")
        return results
    if words:
        for title, keywords, href in OFFICIAL_PLACES:
            if all(any(term.startswith(word) for term in keywords.split()) for word in words):
                results.append({"title": title, "display_title": title, "floor": "Официальный сайт", "official": True,
                                "location_only": False, "href": href, "route": ""})
    return results


def suggestions(request):
    data = maps.campus()
    phrase = request.query("q").strip()[:80]
    way = {}
    for field in ("from", "to"):
        point = data.places.get(request.query(field))
        if point and point.selectable:
            way[field] = point.key
    if back_path := return_path(request):
        way["back"] = back_path
    progress = request.query("progress")
    if progress.isascii() and progress.isdigit() and len(progress) < 4:
        way["progress"] = progress
    floor = data.floors.get(request.query("building") + request.query("floor"))
    way.update(edit_state(request, data))
    return {"results": search_matches(data, phrase, way, floor, search_context(request))[:8] if len(phrase) >= 2 else []}


def display_labels(data, floor):
    corrections = LABEL_AREAS.get(f"{floor.building}{floor.level}", {})
    rows = [(key, x, y, corrections.get(data.places[key].label, area)) for key, x, y, area in floor.labels]
    assigned = {area for _, _, _, area in rows if area is not None}
    labels = []
    for key, x, y, area in rows:
        if area is None:
            # A number just beyond an otherwise unlabelled room edge can be moved inside it.
            nearby = [index for index, (kind, _, (left, top, w, h)) in enumerate(floor.areas)
                      if index not in assigned and kind == "room"
                      and ((left <= x <= left + w and 0 <= y - top - h <= 16)
                           or (top <= y <= top + h and 0 <= left - x <= 16))]
            if len(nearby) == 1:
                area = nearby[0]
                assigned.add(area)
        labels.append((key, x, y, area))
    return labels


def room_marks(data, floor, layout, shapes, selected, endpoints, way, *, canteen_approach=False):
    marks = []
    counts = Counter(area for _, _, _, area in layout)
    for key, x, y, area in layout:
        point = data.places[key]
        dedicated = area is not None and counts[area] == 1
        anchor = f'{x} {y}'
        label_area = ""
        area_kind, path, w, h = "", "", 44, 24
        if area is not None:
            area_kind, path, (left, top, w, h) = floor.areas[area]
            label_area = f' data-campus-area="{area}"' + ("" if dedicated else " data-campus-shared")
            if dedicated:
                x, y = round(left + w / 2, 1), round(top + h / 2, 1)
            path = shapes[area] if dedicated else ""
        label = point.label
        office = maps.OFFICE_NAMES.get((point.floor, point.label))
        name = f' data-campus-office="{e(office)}"' if office else ""
        concise = "Кафе" if label.startswith("Кафе ") else label.partition(" (")[0]
        short = f' data-campus-short="{e(concise)}"' if concise != label else ""
        current = key == selected or key in endpoints
        rotated = bool(re.fullmatch(r"\d+[а-я]?", label, re.IGNORECASE)) and h > 1.45 * w
        fit_width, fit_height = (h, w) if rotated else (w, h)
        font = round(max(6, min(24, (fit_width - 3) / max(3, len(label) * .66), fit_height * .50)), 1)
        rotation = f' transform="rotate(-90 {x} {y})"' if rotated else ""
        target = point_link(data, point, way, fragment="campus-plan")
        hit = (f'<path class="campus-room-hit" d="{e(path)}"/>' if path else
               f'<circle class="campus-room-hit" cx="{x}" cy="{y}" r="7"/>')
        if current:
            hit += (f'<path class="campus-room-focus" d="{e(path)}"/>' if path else
                    f'<circle class="campus-room-point" cx="{x}" cy="{y}" r="12"/>')
        style = " campus-selected" if current else ""
        if key == way.get("from"):
            style += " campus-point-start"
        hall = point.kind == "hall" or (area_kind == "hall" and point.label.startswith("Зал"))
        if hall or point.kind == "service" or office:
            style += " campus-named-room"
        if not path:
            style += " campus-room-without-outline"
        focus = " data-campus-focus" if key == selected else ""
        title = place_title(point)
        if way.get("field") == "from" and point.selectable:
            title = "Начать отсюда · " + title
        facility = {"a1-cafe": "food", "a1-wardrobe": "wardrobe"}.get(key)
        if hall:
            facility = "auditorium"
        beacon = (f'<g data-campus-label-icon class="campus-facility-marker campus-facility-{facility}" hidden aria-hidden="true">'
                  f'<circle r="15"/><path d="{GLYPHS[facility]}"/></g>') if facility else ""
        if canteen_approach and label == "3154":
            facility = "reference"
            hit += f'<path class="campus-route-reference" d="{e(path)}"/>'
        callout = (f'<g data-campus-label-callout class="campus-facility-callout campus-facility-{facility}" hidden aria-hidden="true">'
                   f'<path/><circle/><rect/><text>{e(label)}</text></g>') if facility else ""
        marks.append(f'<a href="{e(target)}" class="campus-room{style}" tabindex="-1" '
                     f'aria-label="{e(title)}"{focus}>'
                     f'{hit}<text x="{x}" y="{y}" font-size="{font}"'
                     f' data-campus-anchor="{anchor}"{label_area}{name}{short}{rotation}>{e(label)}</text>{beacon}{callout}</a>')
    return "".join(marks)


def landmark(data, point, selected, way, origin):
    active = " campus-selected" if point.key == selected else ""
    start = " campus-map-origin" if point.key == origin else ""
    caption = point.label + (" · точка старта" if start else "")
    style = " campus-canteen-stairs" if point.key == maps.CANTEEN_APPROACH else ""
    item = (f'<g class="campus-landmark campus-{point.kind}{active}{start}{style}" transform="translate({point.x} {point.y})">'
            f'<title>{e(caption)}</title><g data-campus-symbol><circle r="15"/>'
            f'<path d="{GLYPHS[point.kind]}"/></g></g>')
    href = point_link(data, point, way, fragment="campus-plan")
    focus = " data-campus-focus" if active else ""
    return f'<a href="{e(href)}" aria-label="{e(caption)}"{focus}>{item}</a>'


def stage_mark(point, letter, end=False):
    if letter:
        symbol = f'<circle r="17"/><text font-size="18">{letter}</text>'
    elif point.kind in GLYPHS:
        symbol = '<circle class="campus-stage-ring" r="21"/>'
    else:
        symbol = f'<circle class="campus-stage-dot" r="{8 if end else 6}"/>'
    return (f'<g class="campus-route-marker" transform="translate({point.x} {point.y})" aria-hidden="true">'
            f'<g data-campus-symbol>{symbol}</g></g>')


def map_frame(drawing, width, height, label, *, grounds=False):
    wide = ' data-campus-wide' if width >= height * 1.8 else ""
    hint = ("" if grounds else '<p class="campus-map-hint" data-campus-label-hint hidden>'
                              'Приблизь карту, чтобы увидеть номера</p>')
    margin = 0 if grounds else 10
    view_box = f"{-margin} {-margin} {width+2*margin} {height+2*margin}"
    region = "Карта территории" if grounds else "Поэтажная схема"
    fit = "Весь кампус" if grounds else "Весь этаж"
    return (
        '<div class="campus-view"><div class="campus-zoom" data-campus-zoom aria-label="Масштаб карты" role="group" hidden>'
        f'<button type="button" class="campus-button" data-campus-fit aria-label="{fit}" title="{fit}">'
        f'{map_icon("fit")}<span>{fit}</span></button>'
        '<button type="button" class="campus-button" data-campus-scale="-1" aria-label="Уменьшить карту">−</button>'
        '<button type="button" class="campus-button" data-campus-scale="1" aria-label="Увеличить карту">+</button>'
        f'</div>{hint}<div class="campus-viewport" tabindex="0" role="region" aria-label="{region}" '
        f'data-campus-viewport{wide}><div class="campus-plan-canvas">'
        f'<svg class="campus-plan" viewBox="{view_box}" role="group" aria-label="{e(label)}">'
        f'{drawing}</svg></div></div></div>'
    )


def plan(data, floor_key, room, stage, way):
    floor = data.floors[floor_key]
    layout = display_labels(data, floor)
    named_services = {area for key, _, _, area in layout if data.places[key].kind == "service"}
    display = display_plans()[floor_key]
    walls, shapes = display["walls"], display["areas"]
    structure = []
    for index, (kind, path, _) in enumerate(floor.areas):
        if shapes[index] is None:
            continue
        named = " campus-area-named" if index in named_services else ""
        structure.append(f'<path class="campus-area campus-area-{e(kind)}{named}" data-campus-index="{index}" d="{e(shapes[index])}"/>')
    line = ""
    corridor = [p for p in stage if p.kind != "location"]
    if len(corridor) > 1:
        points = " ".join(f"{point.x},{point.y}" for point in corridor)
        line = (f'<polyline class="campus-route-halo" points="{points}"/>'
                f'<polyline class="campus-route-line" points="{points}"/>')
    endpoints = {way.get("from"), way.get("to"), way.get("target")}
    origin = way.get("from", maps.ENTRY) if not stage else ""
    labels = room_marks(data, floor, layout, shapes, room, endpoints, way,
                        canteen_approach=bool(stage and stage[-1].key == maps.CANTEEN_APPROACH))
    landmarks = "".join(landmark(data, data.places[key], room, way, origin) for key in floor.places
                        if data.places[key].kind in {"stairs", "entrance", "passage"})
    route_marks = ""
    if stage:
        first, last = stage[0], stage[-1]
        if first != last or last.key != way.get("to"):
            route_marks += stage_mark(first, "А" if first.key == way.get("from") else "")
        if first != last or last.key == way.get("to"):
            finish = last.key == way.get("to") and last.key != maps.CANTEEN_APPROACH
            route_marks += stage_mark(last, "Б" if finish else "", end=True)
    if stage and corridor:
        if stage[0].kind == "location":
            route_marks += stage_mark(corridor[0], "", end=True)
        if stage[-1].kind == "location" and (stage[0].kind != "location" or corridor[-1] != corridor[0]):
            route_marks += stage_mark(corridor[-1], "", end=True)
    drawing = (f'<path class="campus-walls" d="{e(walls)}" fill-rule="evenodd"/>'
               f'{"".join(structure)}{labels}{landmarks}<g data-campus-route-extent>{line}{route_marks}</g>')
    return map_frame(drawing, floor.width, floor.height, data.location(floor_key))


def grounds_plan(data, way, selected, floor):
    grounds = ['<g transform="translate(-226 -35)">'
               '<path class="campus-grounds-bridges" d="M578 128V197L611 201M854 171L850 212M607 276L628 278'
               'M718 289L740 292"/>']
    for key, outline, x, y, title in GROUNDS_OUTLINES:
        shape = f'<path class="campus-grounds-building" fill-rule="evenodd" d="{outline}"/>'
        if title:
            caption = "campus-grounds-letter" if len(title) == 1 else "campus-grounds-caption"
            label_y = 70 if key == "pool" else y
            if key in data.buildings:
                shape += f'<rect class="campus-grounds-building-hit" x="{x-25}" y="{y-22}" width="50" height="44"/>'
            if key == "library":
                caption += " campus-grounds-library"
            shape += f'<text class="{caption}" x="{x}" y="{label_y}" aria-hidden="true">{title}</text>'
        if key in data.buildings:
            floor_key = next(code for code, floor in data.floors.items() if floor.building == key)
            href = floor_url(data, floor_key, **way)
            shape = f'<a href="{e(href)}" aria-label="{e(data.buildings[key])}">{shape}</a>'
        grounds.append(shape)
    entry = point_link(data, data.places[maps.ENTRY], way, fragment="campus-plan")
    grounds.append(
        f'<a class="campus-grounds-entry" href="{e(entry)}" aria-label="Главный вход">'
        '<path class="campus-grounds-leader" d="M528 323V369"/>'
        '<path class="campus-grounds-entry-foot" d="M510 301 540 297 544 334 513 337Z"/>'
        '<g transform="translate(528 381)"><g data-campus-grounds-caption>'
        '<rect x="-84" y="-22" width="168" height="44" rx="15"/>'
        f'<path transform="translate(-63 0)" d="{GLYPHS["entrance"]}"/>'
        '<text x="-45" y="0">Главный вход</text></g></g></a>'
    )
    place = grounds_url(way, place="canteen", floor=floor)
    current = ' aria-current="location"' if selected else ""
    grounds.append(
        f'<a class="campus-grounds-canteen" href="{e(place)}" aria-label="{CANTEEN_NAME}, корпус Ж"{current}>'
        f'<path class="campus-grounds-food-foot" d="{CANTEEN_OUTLINE}"/>'
        '<path class="campus-grounds-leader" d="M782 166H797L836 226"/>'
        '<circle cx="836" cy="226" r="5"/>'
        '<g transform="translate(782 166)"><g data-campus-grounds-caption>'
        '<rect x="-142" y="-26" width="142" height="52" rx="16"/>'
        f'<path transform="translate(-117 0)" d="{GLYPHS["food"]}"/>'
        '<text x="-98" y="0">Столовая</text></g></g></a></g>'
    )
    return map_frame("".join(grounds), 812, 390, "Схема зданий кампуса", grounds=True)


def grounds_page(request, data, floor_key, start, way, browsing, steps, progress, phrase):
    selected = request.query("place") == "canteen"
    at_stairs = selected and request.query("via") == "stairs" and (
        not steps or (way.get("to") == maps.CANTEEN_APPROACH and progress == len(steps)))
    floor = data.floors[floor_key]
    to_floors = floor_url(data, floor_key, **browsing)
    scene = (f'<section class="campus-map-panel" id="campus-plan" aria-label="Территория кампуса">'
             f'<div class="campus-scene"><div class="campus-filters campus-grounds-bar">'
             f'<nav aria-label="Вид карты">{link(to_floors, "Этажи")}'
             '<span aria-current="page">Территория</span></nav>'
             f'<a class="campus-button campus-map-search" href="#campus-search" aria-label="Поиск на карте" '
             f'data-campus-search-focus>{icon("search")}<span>Поиск</span></a></div>'
             f'{grounds_plan(data, browsing, selected, floor)}</div></section>')
    details = ""
    if selected:
        approach = route_to(data.places[maps.CANTEEN_APPROACH], start, way.get("back", ""))
        close = (f'<a class="campus-button campus-close-place" href="{e(grounds_url(browsing, floor=floor))}" '
                 f'aria-label="Закрыть карточку места">{map_icon("close")}</a>')
        if at_stairs or start == maps.CANTEEN_APPROACH:
            action = f'<p class="campus-canteen-guide">{map_icon("stairs")}<span>{CANTEEN_STEPS}</span></p>'
        else:
            origin = 'От главного входа' if start == maps.ENTRY else f'Старт · {e(data.places[start].label)}. Иди'
            action = (f'<p class="campus-small">{origin} до лестницы у 3154. '
                      'Дальше следуй указателям «Столовая».</p>'
                      f'<a class="campus-button campus-primary" href="{e(approach)}">'
                      f'Начать маршрут{icon("arrow")}</a>')
        details = (
            '<section class="campus-place campus-canteen-place" id="campus-place" tabindex="-1">'
            '<div class="campus-place-heading"><div><p class="campus-context">Корпус Ж · рядом со спорткомплексом</p>'
            f'<h2>{CANTEEN_NAME}</h2></div>{close}</div>'
            f'<p class="campus-canteen-level">{map_icon("food")}Студенческий зал на 2-м этаже</p>{action}</section>'
        )
    details += stage_control(data, steps, 0, progress, [], way)
    sidebar = f'<aside class="campus-sidebar" aria-label="Место и маршрут">{details}</aside>' if details else ""
    search = search_panel(request, data, phrase, browsing, floor, data.places[start], grounds=True)
    sources = (f'<div class="campus-reference">{link(CAMPUS_SCHEME, "Схема кампуса")}'
               f'{link(CANTEEN_GUIDE, "Гид Студсоюза")}'
               '<section class="campus-nearby" data-campus-nearby hidden><h2>Рядом с кампусом?</h2>'
               '<p>Разовая проверка в радиусе 500 м от корпуса В. Координаты обрабатывает твой браузер.</p>'
               '<button type="button" class="campus-button" data-nearby-check>Проверить местоположение</button>'
               '<button type="button" class="campus-button" data-nearby-clear hidden>Отключить</button>'
               '<p role="status" data-nearby-status></p></section></div>' )
    back_path = way.get("back", "")
    body = (back_link(back_path)
            + f'<div class="campus-map" data-campus-state="grounds">{mobile_bar(request, back_path)}'
            f'<div class="campus-grid">{scene}{search}'
            f'<div class="campus-details" id="campus-details">{sidebar}{sources}</div></div></div>')
    return page(request, "Карта кампуса", body, "Демонстрационная схема", section="campus-map")


def selected_place(data, point, start, way, arrived):
    if point is None:
        return ""
    name = data.location(point.floor)
    overview = floor_url(data, point.floor, **way)
    close = (f'<a class="campus-button campus-close-place" href="{e(overview)}" '
             f'aria-label="Закрыть карточку места" title="Закрыть карточку места">{map_icon("close")}</a>')
    heading = (f'<div class="campus-place-heading"><div><p class="campus-context">{e(name)}</p>'
               f'<h2>{e(place_title(point))}</h2></div>{close}</div>')
    section = f'<section class="campus-place" id="campus-place" tabindex="-1">{heading}'
    if point.key == maps.CANTEEN_APPROACH:
        route = route_to(point, start.key, way.get("back", ""))
        canteen = grounds_url(way, place="canteen", floor=data.floors[point.floor])
        approach = link(route, "Маршрут до лестницы", "campus-button campus-primary") if route else ""
        return (f'{section}<p>{CANTEEN_STEPS}</p><div class="campus-place-actions">{approach}'
                f'{link(canteen, "Столовая на карте", "campus-inline-action")}</div></section>')
    if point.kind in {"stairs", "passage"}:
        options = []
        for nearby, _ in data.neighbors.get(point.key, ()):
            other = data.places[nearby]
            if other.floor != point.floor:
                href = floor_url(data, other.floor, room=other.key, **way)
                options.append(f'<li>{link(href, data.location(other.floor))}</li>')
        return (f'{section}<p class="campus-small">Куда ведёт</p>'
                f'<ul class="campus-transfers">{"".join(options)}</ul></section>')
    if point == start:
        message = "Маршрут завершён" if arrived else "Точка старта"
        choose = "Найти другое место" if arrived else "Выбрать, куда идти"
        return (f'{section}<p class="campus-tag">{map_icon("check" if arrived else "pin")}{message}</p>'
                f'<a class="campus-button campus-primary" href="#campus-query" data-campus-search-focus>'
                f'{choose}{icon("arrow")}</a></section>')
    origin = point_link(data, point, {**way, "field": "from"})
    route = route_to(point, start.key, way.get("back", ""))
    note = ('<p class="campus-small">Маршрут приведёт к отметке в коридоре. '
            'Точное положение двери на плане не указано.</p>' if point.kind == "location" else "")
    return (f'{section}{note}<p class="campus-place-start">Старт маршрута · {e(start.label)}</p>'
            f'<div class="campus-place-actions"><a class="campus-button campus-primary" href="{e(route)}">'
            f'Маршрут сюда{icon("arrow")}</a>{link(origin, "Начать отсюда", "campus-button")}</div></section>')


def point_options(data, name, label, selected):
    groups = []
    for building, building_name in data.buildings.items():
        options = []
        points = sorted((place for place in data.choices.values() if data.floors[place.floor].building == building),
                        key=lambda place: (data.floors[place.floor].level, place.kind != "entrance", place.label))
        for point in points:
            floor = data.floors[point.floor].level
            choice = " selected" if point.key == selected else ""
            caption = place_title(point) if (point.floor, point.label) in maps.OFFICE_NAMES else point.label
            options.append(f'<option value="{e(point.key)}" data-campus-label="{e(point.label)}" data-campus-search="{e(maps.normalize(caption + " " + data.location(point.floor)))}"{choice}>'
                           f'{e(caption)} · {floor} эт.</option>')
        groups.append(f'<optgroup label="{e(building_name)}">{"".join(options)}</optgroup>')
    return (f'<label>{e(label)}<select name="{name}" required>'
            f'<option value="">Выбери точку</option>{"".join(groups)}</select></label>')


def step_text(data, segment, upcoming):
    start, end = segment[0], segment[-1]
    if (end.floor, end.label) in maps.OFFICE_NAMES:
        destination = f"кабинета {end.label}"
    elif end.key == maps.CANTEEN_APPROACH:
        destination = "лестницы у 3154"
    else:
        destination = f"аудитории {end.label}" if end.kind == "room" else f"«{end.label}»"
    has_line = sum(p.kind != "location" for p in segment) > 1
    direction = f"Иди по линии до {destination}." if has_line else f"Ориентир на плане — {destination}."
    if upcoming:
        next_point = upcoming[0]
        next_floor = data.floors[next_point.floor]
        previous_floor = data.floors[end.floor]
        if end.kind == "stairs":
            direction = f"Следуй по линии. Ориентир — «{end.label}». " if has_line else ""
            verb = "Поднимись" if next_floor.level > previous_floor.level else "Спустись"
            direction += f"{verb} по этой лестнице на {next_floor.level}-й этаж."
            if abs(next_floor.level - previous_floor.level) == 2:
                direction += " Пройди промежуточную площадку 3-го этажа по той же лестнице."
        else:
            approach = "Иди по линии к переходу. " if has_line else ""
            direction = f"{approach}Через переход попадёшь в {data.buildings[next_floor.building]}, на {next_floor.level}-й этаж."
    if start == end and not upcoming:
        return f"Ты уже в точке назначения. {place_title(end)}."
    if end.kind == "location" and not upcoming:
        direction = f"От отмеченной точки в коридоре найди «{end.label}» по указателям. Помещение отмечено буквой Б."
        if has_line:
            direction = "Дойди по линии до её конца. " + direction
    if start.kind == "location":
        direction = "От помещения А найди отмеченную точку в коридоре по плану. " + direction
    if end.key == "v4-place-25" and not upcoming:
        direction = "Ты у площадки восточной лестницы. Зал №6 отмечен буквой Б. На плане для него указан выход в корпус Г. Дверь со стороны площадки не подтверждена."
    if start.key == "v4-place-25":
        direction = "Проверь на плане доступ из зала к восточной лестнице. " + direction
    if end.key == "g3-place-57" and not upcoming:
        direction = "Линия заканчивается у лестницы. Помещение 3180 находится в отдельной секции. Проход в секцию на плане не указан."
    if start.key == "g3-place-57":
        direction = "Выход из секции 3180 на схеме не указан. " + direction
    return direction


def directions(data, floor, start, end, selected, steps, progress, error, back="", *, editing=False):
    if not steps and not error:
        return ""
    target = end or (selected if selected and data.places[selected].selectable else "")
    fields = point_options(data, "from", "Откуда", start) + point_options(data, "to", "Куда", target)
    swap_fields = ('<button type="button" class="campus-button" data-campus-swap hidden>Поменять точки</button>')
    form = (f'<form id="campus-route-fields" method="get" action="{maps.PATH}#campus-plan" '
            f'class="campus-route-form">{hidden("back", back) if back else ""}{hidden("remember_start", "1")}{fields}'
            '<div class="campus-actions"><button type="submit" class="campus-button campus-primary">'
            'Построить маршрут</button>'
            f'{swap_fields}</div></form>')
    output = ""
    if steps:
        way = {"from": start, "to": end, "back": back}
        rows = []
        for index, segment in enumerate(steps, 1):
            href = url("campus-plan", step=index, **way)
            label = data.location(segment[0].floor)
            current = ' aria-current="step"' if index == progress else ""
            rows.append(f'<li{current}>{link(href, label)}</li>')
        share = url("campus-plan", step=progress, **{k: v for k, v in way.items() if k != "back"})
        output = (f'<h3>Все шаги</h3><ol class="campus-steps">{"".join(rows)}</ol>'
                  f'<div class="campus-route-tools"><button type="button" class="campus-button campus-text-button" '
                  f'data-campus-share="{e(share)}" hidden>Скопировать ссылку</button>'
                  f'{link(floor_url(data, floor, back=back, **{"from": start}), "Сбросить маршрут", "campus-button")}</div>'
                  '<p class="campus-small" data-campus-share-status role="status"></p>'
                  '<input data-campus-share-url readonly hidden aria-label="Ссылка на маршрут">')
    if error:
        return f'<section id="campus-route" class="campus-route campus-route-recovery">{form}</section>'
    expanded = ' open' if editing else ''
    return (f'<details id="campus-route" class="campus-disclosure campus-route"{expanded}>'
            f'<summary>Настройки маршрута</summary><div class="campus-route-settings">{output}'
            f'<details class="campus-disclosure campus-edit-route"{expanded}><summary>Изменить точки</summary>'
            f'{form}</details></div></details>')


def stage_control(data, steps, active, progress, floor_steps, way):
    if not steps:
        return ""
    start, finish = data.places[way["from"]], data.places[way["to"]]
    summary = f'{e(start.label)} → {e(finish.label)}'
    to_canteen = finish.key == maps.CANTEEN_APPROACH
    if not active:
        back = link(url("campus-plan", step=progress, **way), "Продолжить маршрут", "campus-button campus-primary")
        choices = "".join(link(url("campus-plan", step=index, **way), f"К шагу {index}", "campus-button")
                          for index in floor_steps if index != progress)
        return (f'<section class="campus-paused" data-campus-mode="browse" aria-label="Приостановленный маршрут">'
                f'<p class="campus-small">Маршрут приостановлен · шаг {progress} из {len(steps)}</p>'
                f'<p class="campus-endpoints">{summary}</p><div class="campus-pause-actions">{back}{choices}</div></section>')
    upcoming = steps[active] if active < len(steps) else None
    segment = steps[active - 1]
    location = data.location(segment[0].floor)
    approach = not upcoming and to_canteen
    if to_canteen:
        summary = "К столовой «Трёшка»"
    previous = ""
    if active > 1:
        href = url("campus-plan", step=active - 1, **way)
        previous = (f'<a class="campus-button campus-prev-step" href="{e(href)}" title="Предыдущий шаг" '
                    f'aria-label="Предыдущий шаг">{map_icon("back")}</a>')
    if upcoming:
        next_floor = data.floors[upcoming[0].floor]
        if next_floor.building == data.floors[segment[0].floor].building:
            action = f"На {next_floor.level}-й этаж"
        else:
            action = f"Дальше · {data.buildings[next_floor.building]}"
        target, glyph = url("campus-plan", step=active + 1, **way), segment[-1].kind
    elif approach:
        action, glyph = "Дальше к столовой", "stairs"
        target = grounds_url({**way, "progress": active}, place="canteen", via="stairs", floor=data.floors[finish.floor])
    else:
        action, glyph = "Завершить маршрут", "pin"
        target = floor_url(data, finish.floor, room=finish.key, arrived="1", back=way.get("back"), **{"from": finish.key})
    mark = map_icon(glyph if glyph in GLYPHS else "pin")
    action_icon = icon("arrow") if upcoming or approach else map_icon("check")
    forward = (f'<a class="campus-button campus-primary" href="{e(target)}">{e(action)}'
               f'{action_icon}</a>')
    track = (f'<progress class="campus-progress" max="{len(steps)}" value="{active}" '
             'aria-label="Шаг маршрута"></progress>') if len(steps) > 1 else ""
    continuation = '<p class="campus-small">После спуска следуй указателям «Столовая».</p>' if approach else ""
    detour = ""
    if start.floor == finish.floor and len(steps) > 1:
        official = data.floors[start.floor].official
        detour = ('<p class="campus-small">На плане этого этажа отдельные участки не соединены. '
                  'Маршрут проходит через другой этаж или корпус. '
                  + link(official, "Свериться с официальным планом") + '</p>')
    elif start.floor[0] == finish.floor[0] and any(s[0].floor[0] != start.floor[0] for s in steps):
        detour = ('<p class="campus-small">Доступ к этой части корпуса на опубликованной схеме '
                  'проходит через соседний корпус.</p>')
    if any(p.kind == "location" for p in (segment[0],segment[-1])) and start != finish:
        detour += ('<p class="campus-small">Точное положение двери не указано. '
                   + link(data.floors[segment[0].floor].official,"Официальный план этажа") + '</p>')
    overview = floor_url(data, segment[0].floor, progress=active, **way)
    edit = url("campus-route-fields", step=active, edit="1", **way)
    return (f'<section class="campus-current" data-campus-mode="route">'
            f'<div class="campus-step-heading"><p class="campus-context">Шаг {active} из {len(steps)}</p>'
            f'<p class="campus-endpoints">{summary}</p></div>{track}'
            f'<h2>{mark}{e(location)}</h2><p class="campus-instruction">{e(step_text(data, segment, upcoming))}</p>'
            f'<p class="campus-small">Начало шага · {e(segment[0].label)}</p>{detour}{continuation}'
            f'<div class="campus-step-actions">{previous}{forward}</div><div class="campus-route-tools">'
            f'<a class="campus-button" href="{e(edit)}" data-campus-edit>Изменить точки</a>'
            f'{link(overview, "Обзор этажа", "campus-button")}</div></section>')


def legend(data, floor, stage):
    kinds = {data.places[key].kind for key in floor.places}
    marks = [(kind, label) for kind, label in (("entrance", "Вход"), ("stairs", "Лестница"),
                                             ("passage", "Переход")) if kind in kinds]
    if "a1-cafe" in floor.places:
        marks.extend((("food", "Кафе"), ("wardrobe", "Гардероб")))
    if "hall" in kinds:
        marks.append(("auditorium", "Зал"))
    labels = [f'<li class="campus-{kind}">{map_icon(kind)}{label}</li>' for kind, label in marks]
    if stage:
        labels.append('<li><span class="campus-line-sample" aria-hidden="true"></span>Текущий участок</li>')
    return f'<ul class="campus-legend" aria-label="Обозначения на плане">{"".join(labels)}</ul>'


def route_summary(data, floor, start, end, steps, active, remembered):
    if not steps:
        return ""
    start_point, end_point = data.places[start], data.places[end]
    destination = CANTEEN_NAME if end == maps.CANTEEN_APPROACH else place_title(end_point)
    location = "До лестницы у 3154" if end == maps.CANTEEN_APPROACH else data.location(end_point.floor)
    remaining = Counter(segment[-1].kind for segment in steps[active - 1:-1]) if active else {}
    landmarks = [count_label(remaining[kind], *labels)
                 for kind, labels in (("stairs", ("лестница", "лестницы", "лестниц")),
                                      ("passage", ("переход", "перехода", "переходов")))
                 if remaining.get(kind)]
    ahead = "Впереди · " + " и ".join(landmarks) if landmarks else ""
    if active and not ahead:
        ahead = "Последний участок по схеме"
    saved = f'<p class="campus-small">Сохранённый старт · {e(start_point.label)}</p>' if remembered else ""
    return ('<section class="campus-route-summary" aria-label="О маршруте">'
            f'<p><strong>Куда</strong> {e(destination)} · {e(location)}</p>'
            f'<p><strong>На схеме</strong> {e(data.location(floor))}</p>'
            + (f'<p class="campus-small">{e(ahead)}</p>' if ahead else "") + saved + '</section>')


def start_cookie(request):
    if request.user:
        account = digest(request.user["id"])[:16]
        return request.session_cookie + "_campus_start_" + account
    return ""


def index(request, db):
    data = maps.campus()
    view = request.query("view")
    if view == "schedule":
        return campus_demo.schedule(request, data)
    if view == "directory":
        return campus_demo.directory(request, data)
    places = data.choices
    cookie = start_cookie(request)
    saved = request.cookie(cookie) if cookie else ""
    saved = saved if saved in places else ""
    supplied = request.query("from")
    start, end = request.query("from", saved or maps.ENTRY), request.query("to")
    selected = data.places.get(request.query("room"))
    if selected and selected.kind == "junction":
        selected = None
    error = ""
    path = []
    if end:
        if start not in places or end not in places:
            error = "Выбери точки маршрута из списка."
        elif start == end:
            path = [places[start]]
        else:
            path = data.navigation.route(start, end)
            if not path:
                error = "Между этими точками пока нет маршрута на схеме."
    start = start if start in places else maps.ENTRY
    end = end if end in places else ""
    way = {"from": start, "to": end} if end else {"from": start} if start != maps.ENTRY else {}
    back_path = return_path(request)
    if back_path:
        way["back"] = back_path
    if (cookie and request.method == "GET" and supplied in places and not error and request.query("remember_start") == "1"
            and request.env.get("HTTP_SEC_FETCH_SITE") != "cross-site"):
        request.set_cookie(cookie, start if start != maps.ENTRY else "", START_MAX_AGE if start != maps.ENTRY else 0)
        # Don’t replay a preference change when a browser opens an old route again.
        from .web import redirect
        context = {key: request.query(key) for key in ("building", "floor", "view", "place", "via", "target", "step")}
        fragment = "campus-plan" if end else "campus-place" if view == "grounds" else "campus-query"
        return redirect(url(fragment, **way, **context, **({"from": start} if "from" not in way else {})))
    steps = maps.stages(path)
    levels = [segment[0].floor for segment in steps]
    step = stage_number(request.query("step"), len(steps))
    progress = step or stage_number(request.query("progress"), len(steps))
    building, level = request.query("building"), request.query("floor")
    if step:
        floor_key = levels[step - 1]
    elif selected:
        floor_key = selected.floor
    elif building in data.buildings:
        choice = f"{building}{level}"
        floor_key = choice if choice in data.floors else next(key for key, value in data.floors.items()
                                                              if value.building == building)
    else:
        floor_key = levels[(progress or 1) - 1] if levels else data.places[start].floor
    if step or (selected and selected.floor != floor_key):
        selected = None
    floor_steps = [index for index, key in enumerate(levels, 1) if key == floor_key]
    if steps and not progress:
        progress = floor_steps[0] if floor_steps else 1
    exploring = not step and (selected or building in data.buildings)
    active = progress if progress in floor_steps and not exploring else 0
    editing = edit_state(request, data)
    if not end and "target" not in editing:
        if request.query("place") == "canteen" and view == "grounds":
            editing["target"] = "canteen"
        elif selected and selected.selectable and selected.key != start:
            editing["target"] = selected.key
    browsing = {**way, **editing}
    if progress:
        browsing["progress"] = progress
    phrase = request.query("q").strip()[:80]
    if phrase and request.query("pick") == "1":
        results = search_matches(data, phrase, browsing, data.floors[floor_key], search_context(request))
        if len(results) == 1 and not (results[0].get("official") or results[0].get("unavailable_start")):
            from .web import redirect
            return redirect(results[0]["href"])
    if request.query("view") == "grounds":
        return grounds_page(request, data, floor_key, start, way, browsing, steps, progress, phrase)
    stage = steps[active - 1] if active else ()
    floor = data.floors[floor_key]
    directory = sorted((point for point in data.directory() if point.floor == floor_key), key=lambda point: point.label)
    public_places = "".join(f'<li>{link(point_link(data, point, browsing), point.label)}</li>'
                            for point in directory)
    list_view = f'<details class="campus-point-list"><summary>Помещения на схеме</summary><ul>{public_places}</ul></details>'
    feedback = (f'<div class="campus-route-error" role="alert"><h2>Маршрут не построен</h2><p>{e(error)}</p>'
                f'{link("#campus-route", "Изменить точки")}</div>') if error else ""
    selected_key = selected.key if selected else ""
    origin = data.places[start]
    arrived = not end and request.query("arrived") == "1"
    search = search_panel(request, data, phrase, browsing, floor, origin, navigating=bool(active))
    detail = (feedback + selected_place(data, selected, origin, browsing, arrived)
              + stage_control(data, steps, active, progress, floor_steps, way)
              + directions(data, floor_key, start, end, selected_key, steps, progress, error, way.get("back", ""),
                           editing=request.query("edit") == "1"))
    sidebar = f'<aside class="campus-sidebar" aria-label="Место и маршрут">{detail}</aside>' if detail else ""
    state = "route" if active else "place" if selected else "overview"
    summary = route_summary(data, floor_key, start, end, steps, active, saved and not supplied)
    scene = (f'<section class="campus-map-panel" id="campus-plan" aria-label="{e(data.location(floor_key))}">'
             f'{summary}<div class="campus-scene">{picker(data, floor, browsing)}'
             f'{plan(data, floor_key, selected_key, stage, browsing)}</div></section>')
    workspace = scene + search if active else search + scene
    body = (
        f'<div class="campus-map" data-campus-state="{state}">'
        + mobile_bar(request, back_path) + '<div class="campus-grid">' + workspace
        + f'<div class="campus-details" id="campus-details">{sidebar}{legend(data, floor, stage)}'
        + '<div class="campus-reference">'
        + f'{list_view}{link(floor.official, "Официальный план этажа")}</div></div></div>'
        + '<p class="campus-source">По данным опубликованных схем. Открытость проходов на месте не проверялась. '
        + link(maps.OFFICIAL, "Официальная карта") + '</p><nav class="campus-related" aria-label="Сервисы кампуса">'
        + link(campus_demo.SCHEDULE, "Расписание кампуса · демо")
        + link(campus_demo.DIRECTORY, "Сотрудники · демо") + "</nav></div>"
    )
    body = back_link(back_path) + body
    return page(request, "Карта кампуса", body, "Демонстрационная схема", section="campus-map")


def stage_number(value, length):
    if value.isascii() and value.isdigit() and len(value) <= 3 and 1 <= int(value) <= length:
        return int(value)
    return 0
