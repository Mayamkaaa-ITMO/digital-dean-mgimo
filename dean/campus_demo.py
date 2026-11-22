"""Synthetic schedule for the campus map."""
from . import campus_map as maps
from .ui import e, link, page, panel
from urllib.parse import urlencode

SCHEDULE = maps.PATH + "?view=schedule"
DIRECTORY = maps.PATH + "?view=directory"
GROUP = "ДЕМО-КАМПУС-01"
# Date and wall-clock times are fixed examples in Europe/Moscow.
LESSONS = (
    ("23 ноября", "09:00–10:20", "Практикум по переговорам", "b", 1, "1012"),
    ("23 ноября", "10:35–11:55", "Академическое письмо", "b", 2, "2018а"),
    ("24 ноября", "09:00–10:20", "Международные организации", "n", 4, "Зал 423"),
    ("24 ноября", "12:10–13:30", "Работа с источниками", "b", 2, "2047"),
)
LESSON_ANCHORS = tuple(f"demo-lesson-{index}" for index in range(1, len(LESSONS) + 1))
LESSON_RETURN_PATHS = frozenset(f"{SCHEDULE}#{anchor}" for anchor in LESSON_ANCHORS)
STAFF = (
    ("Алина Берестова", "Консультации по учебным заданиям", "b", 2, "2018а"),
    ("Тимур Луговой", "Вопросы о демонстрационной программе", "b", 2, "2047"),
)


def exact_room(data, building, level, label):
    floor = data.floors.get(f"{building}{level}")
    if not floor or floor.building != building or floor.level != level:
        return None
    wanted = {maps.normalize(label), maps.normalize(label, latin_b="б")}
    matches = [place for place, name, _ in data.search_index
               if place.floor == f"{building}{level}" and name in wanted]
    return matches[0] if len(matches) == 1 else None


def room_info(data, building, level, label, *, back=""):
    point = exact_room(data, building, level, label)
    if not point:
        return e(label) + " · Место на карте не подтверждено"
    query = {"building": building, "floor": level}
    if back:
        query["back"] = back
    query["room"] = point.key
    target = maps.PATH + "?" + urlencode(query) + "#campus-place"
    caption = "Найти на карте" if point.route_endpoint else "Посмотреть на плане"
    return (f'{e(label)} · {e(data.location(point.floor))}<br>'
            + link(target, caption, "rw-primary-link"))


def schedule(request, data):
    rows = []
    for anchor, (day, time, title, building, level, label) in zip(LESSON_ANCHORS, LESSONS):
        destination = room_info(data, building, level, label, back=f"{SCHEDULE}#{anchor}")
        rows.append(f'<li id="{anchor}"><div><strong>{e(title)}</strong><p>{e(day)} · {e(time)}</p>'
                    f'<p>{destination}</p></div></li>')
    content = panel(GROUP, '<p>23–29 ноября 2026 · московское время. Группа и занятия вымышлены.</p>'
                    + '<ul class="rows">' + "".join(rows) + '</ul>')
    content += '<p>' + link("https://ruz.mgimo.ru/ruz/", "Официальная страница расписания") + '</p>'
    content += '<p>' + link(maps.PATH, "К карте кампуса") + '</p>'
    return page(request, "Расписание кампуса · демо", content)


def directory(request, data):
    rows = [f'<li><div><strong>{e(name)}</strong><p>{e(role)}</p><p>{room_info(data, building, level, label)}</p></div></li>'
            for name, role, building, level, label in STAFF]
    content = panel("Консультации", '<p>Все имена, назначения и кабинеты сотрудников в этом примере вымышлены.</p>'
                    + '<ul class="rows">' + "".join(rows) + '</ul>')
    return page(request, "Справочник сотрудников · демо", content + '<p>' + link(maps.PATH, "К карте кампуса") + '</p>')
