"""Fictional campus contacts with explicit map point bindings."""
from os import getenv
from urllib.parse import urlencode

from . import campus_map
from .domain import DomainError
from .event_tickets import DEMO
from .ui import back, button, e, empty, input_field, link, page, panel, select_field

PATH = "/campus-directory"
LABEL = "Люди и подразделения"
TYPES = {"teacher": "Преподаватели", "department": "Подразделения"}
CONTACTS = (
    ("teacher", "Лада Синтетова", "Преподаватель проектной практики", "b1-1012"),
    ("teacher", "Станислав Числов", "Преподаватель анализа данных", "b2-2018a"),
    ("department", "Учебная лаборатория макетов", "Консультации по учебным проектам", "b1-1012"),
    ("department", "Бюро учебных встреч", "Организация открытых семинаров", "n4-423"),
)


def available(user):
    return getenv("DEAN_EXT_CAMPUS_DIRECTORY") == "1" and (user["university_id"] == DEMO or getenv("DEAN_PRODUCT") == "1")


def contacts(db,user):
    return db.execute("SELECT * FROM campus_contact WHERE university_id=? ORDER BY name",(user["university_id"],)).fetchall()


def contact_card(row):
    details = '<p>'+e(row["description"])+'</p><p>'+e(row["office"])+'</p>'
    if row["hours"]:
        details += '<p>Часы приёма · '+e(row["hours"])+'</p>'
    if row["email"]:
        details += '<p>Почта · '+e(row["email"])+'</p>'
    campus = campus_map.campus()
    if row["map_place"] in campus.places and campus_map.route(campus,campus_map.ENTRY,row["map_place"]):
        url = campus_map.PATH+"?"+urlencode({"to":row["map_place"]})+"#campus-plan"
        details += '<p>'+link(url,"Маршрут к кабинету")+'</p>'
    return details


def index(request, db):
    if not available(request.user):
        raise DomainError(404, "Демонстрационный справочник недоступен.")
    from . import campus_lab

    campus_enabled = campus_lab.enabled(request.user)
    previous = (back("/staff", "В кабинет сотрудника") if request.user["role"] == "staff"
                else back("/all-services", "К сервисам"))
    query = request.query("q").strip()[:100]
    kind = request.query("kind")
    controls = input_field("q", "Имя или подразделение", query, 'maxlength="100"')
    controls += select_field("kind", "Тип", [("", "Все"), *TYPES.items()], kind)
    filters = f'<form class="inline" action="{PATH}" method="get">{controls}{button("Найти")}</form>'
    if request.user["university_id"] != DEMO:
        query_words = query.casefold().split()
        matches = [r for r in contacts(db,request.user) if (not kind or r["kind"]==kind)
                   and all(word in f'{r["name"]} {r["description"]} {r["office"]}'.casefold() for word in query_words)]
        rows = "".join(f'<li><div>{link(f"{PATH}/contacts/{r['id']}",r["name"])}<p>{e(r["office"])}</p></div></li>'
                       for r in matches)
        body = (back("/campus-lab","К кампусу") if campus_enabled else previous) + filters
        if campus_enabled:
            body += '<p>'+link("/coworking","Проверить близость к настроенной площадке")+'</p>'
        body += '<ul class="rows">'+rows+'</ul>' if rows else empty("Контакты не найдены.")
        return page(request,LABEL,body)
    campus = campus_map.campus()
    rows = ""
    for person_id, (category, name, description, key) in enumerate(CONTACTS,1):
        if kind and category != kind:
            continue
        place = campus.places.get(key)
        address = f"{campus.location(place.floor)} · {place.label}" if place else "Адрес уточняется"
        searchable = f"{name} {description} {address}".casefold()
        if any(word not in searchable for word in query.casefold().split()):
            continue
        target = ""
        if place and campus_map.route(campus, campus_map.ENTRY, place.key):
            url = campus_map.PATH + "?" + urlencode({"to": place.key}) + "#campus-plan"
            target = f'<p>{link(url, "Маршрут к кабинету")}</p>'
        heading = link(f"{PATH}/contacts/{person_id}",name) if campus_enabled else e(name)
        rows += f'<li><div><strong>{heading}</strong><p>{e(description)}</p><p>{e(address)}</p>{target}</div></li>'
    items = f'<ul class="rows">{rows}</ul>' if rows else empty("В справочнике ничего не найдено.")
    presence = campus_lab.directory_status(request, db) if campus_enabled else ""
    return page(request, LABEL, presence + previous + filters
                + panel("Демонстрационный справочник", "<p>Имена и подразделения вымышлены.</p>" + items))


def contact(request, db, number):
    from . import campus_lab
    if not available(request.user):
        raise DomainError(404, "Такой карточки в справочнике нет.")
    if request.user["university_id"] != DEMO:
        row = db.execute("SELECT * FROM campus_contact WHERE university_id=? AND id=?",
                         (request.user["university_id"],int(number))).fetchone()
        if not row:
            raise DomainError(404,"Такой карточки в справочнике нет.")
        return page(request,row["name"],back(PATH,"К справочнику")+panel("Контакт",contact_card(row)))
    campus_lab.access(request.user)
    if not 1 <= int(number) <= len(CONTACTS):
        raise DomainError(404, "Такой карточки в справочнике нет.")
    category,name,description,key = CONTACTS[int(number)-1]
    office_hours, mail = (
        ("Вторник, 15:00–16:00", "practice"),
        ("Среда, 12:00–13:00", "data"),
        ("Будни, 10:00–17:00", "lab"),
        ("Будни, 10:00–18:00", "events"),
    )[int(number)-1]
    campus=campus_map.campus()
    place=campus.places[key]
    entries = (f'<p>{e(description)}</p><p>{e(campus.location(place.floor))} · {e(place.label)}</p>'
               f'<p>Часы приёма · {office_hours}</p><p>Учебная почта · {mail}@campus.example</p>')
    target=campus_map.PATH+"?"+urlencode({"to":key})+"#campus-plan"
    return page(request,name,back(PATH,"В справочник")+panel("Контакты",entries+link(target,"Построить маршрут")),"Демонстрационная карточка")
