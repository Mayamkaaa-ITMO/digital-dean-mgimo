"""Entry points for the separate campus pilot and voluntary campus presence."""
from base64 import b64encode
from datetime import timedelta
from os import getenv

from . import appearance, campus_directory, campus_map, campus_pay, room_occupancy
from .clock import now
from .db import write
from .domain import DomainError, role, timestamp
from .event_tickets import DEMO, png
from .ui import back, button, e, form, hidden, link, page, panel

PATH = "/campus-lab"
LECTURES = "/lectures"
TAG = "MGIMO-CAMPUS-PAY:KIOSK:01"


def enabled(user):
    return bool(user and getenv("DEAN_CAMPUS_LAB") == "1"
                and (user["university_id"] == DEMO or getenv("DEAN_PRODUCT") == "1"))


def access(user):
    if not enabled(user):
        raise DomainError(404, "Сервисы кампуса сейчас недоступны.")


def home(request, db):
    access(request.user)
    staff = request.user["role"] == "staff"
    entries = [
        (PATH + "/events" if staff else "/activities",
         "Мероприятия", "Создай событие и опубликуй регистрацию." if staff else "Запишись на событие и открой его в расписании."),
        (campus_map.PATH + "?to=b1-1012#campus-plan", "Навигатор", "Маршрут от входа до аудитории 1012."),
        (PATH + "/terminal" if staff else campus_pay.PATH, "Кампус Pay", "Настрой NFC-метку кассы." if staff else "Оплати покупку учебными баллами."),
        ("/coworking", "Загрузка коворкинга", "Входы, выходы, тепловая карта и сверка замеров."),

        (campus_directory.PATH, "Люди и подразделения", "Найди кабинет и проверь своё присутствие в кампусе."),
        ("/lecture-library", "Видеотека", "Курсы, субтитры, конспекты и практика."),
        (LECTURES, "Эфиры группы", "Прямой эфир, запись и домашние задания."),
    ]
    if room_occupancy.available(request.user):
        entries.insert(4, (room_occupancy.PATH, room_occupancy.LABEL, "Свободные места и тепловая карта по зонам."))
    if not campus_pay.installed(request.user):
        entries = [entry for entry in entries if entry[1] != "Кампус Pay"]
    if not campus_directory.available(request.user):
        entries = [entry for entry in entries if entry[0] != campus_directory.PATH]
    if not staff:
        entries.insert(3, ("/calendar/import/photo", "Расписание по фото", "Проверь распознанные пары и добавь в календарь."))
    icons = {"Мероприятия": "calendar", "Навигатор": "map", "Кампус Pay": "qr",
             "Загрузка коворкинга": "building", room_occupancy.LABEL: "services",
             "Люди и подразделения": "profile", "Видеотека": "book", "Эфиры группы": "book", "Расписание по фото": "file"}
    body = '<div class="lab-grid dean-lab-tiles">' + "".join(
        appearance.service_tile(url, title, note, icons[title]) for url, title, note in entries) + '</div>'
    return page(request, "Кампус", body, "Сервисы университетского кампуса", section="campus-lab")


def terminal(request, db):
    access(request.user)
    if not campus_pay.installed(request.user):
        raise DomainError(404,"Кампус Pay пока недоступен.")
    if request.user["role"] != "staff":
        raise DomainError(403, "Настройка кассы доступна сотруднику.")
    url = request.origin + campus_pay.TERMINAL
    qr = b64encode(png(url)).decode("ascii")
    writer = panel("Метка кассы",
                   '<p>В Chrome на Android с NFC запиши адрес этой кассы на пустую NDEF-метку.</p>'
                   f'<button type="button" data-write-terminal="{TAG}" data-terminal-url="{e(url)}">'
                   'Записать NFC-метку</button><p data-terminal-result role="status"></p>')
    code = panel("QR для телефона",
                 '<p>Открой кассу с метки или отсканируй QR камерой. Покупку студент подтверждает в своём кабинете.</p>'
                 f'<img src="data:image/png;base64,{qr}" width="205" height="205" alt="QR учебной кассы">'
                 f'<p><code>{e(url)}</code></p>')
    body = writer + code
    return page(request, "Кампусная касса", back(PATH, "К кампусу") + body)


def share(request, db):
    access(request.user)
    state = request.field("state")
    if state not in {"near", "far", "unknown", "off"}:
        raise DomainError(422, "Повтори определение местоположения.")
    with write(db):
        db.execute("DELETE FROM lab_presence WHERE actor=? OR expires<=?", (request.user["id"], timestamp()))
        if state != "off":
            db.execute("INSERT INTO lab_presence VALUES (?,?,?)",
                       (request.user["id"], state, timestamp(now()+timedelta(minutes=5))))
    return 303, b"", [("Location", campus_directory.PATH)]


def directory_status(request, db):
    controls = ('<div data-lab-geo><p>Демонстрационная зона кампуса — круг радиусом 500 м. '
                'Координаты обрабатывает браузер.</p>'
                '<button type="button" data-geo-start>Определять, в кампусе ли я</button> '
                '<button type="button" data-geo-stop hidden>Остановить</button>'
                '<p data-geo-result role="status">Определение выключено.</p>')
    share_form = form(request, PATH + "/presence", hidden("state", "") +
                      '<button data-geo-share disabled>Показать мой статус в справочнике на 5 минут</button>')
    controls += share_form + '</div>'
    rows = db.execute("""SELECT a.full_name,p.state,p.expires FROM lab_presence p JOIN account a ON a.id=p.actor
                         WHERE a.university_id=? AND p.expires>? ORDER BY a.full_name""",
                      (request.user["university_id"], timestamp())).fetchall()
    labels = {"near":"В кампусе", "far":"Вне кампуса", "unknown":"Местоположение неточное"}
    listed = "".join(f'<li data-presence-expires="{e(r["expires"])}">{e(r["full_name"])} · {labels[r["state"]]}</li>' for r in rows)
    withdraw = form(request, PATH + "/presence", hidden("state","off")+button("Скрыть мой статус")) if db.execute(
        "SELECT 1 FROM lab_presence WHERE actor=? AND expires>?", (request.user["id"], timestamp())).fetchone() else ""
    return panel("Моё присутствие", controls) + panel("Поделились статусом", ('<ul>'+listed+'</ul>' if rows else '') + '<p data-presence-empty'+(' hidden' if rows else '')+'>Пока никто не поделился статусом.</p>' + withdraw)

def events(request, db):
    from . import staff_management
    access(request.user)
    role(request.user, "staff")
    rows = [r for r in staff_management.resources(db, request.user) if r["kind"]=="activity"]
    items = "".join(
        f'<li><div>{link("/staff/manage/resources/" + row["id"], row["title"])}'
        f'<p class="meta">{e(staff_management.RESOURCE_STATES[row["state"]])}</p></div></li>'
        for row in rows
    )
    controls = link("/staff/manage/resources/new?kind=activity","Создать мероприятие","dean-action-link")
    body = back(PATH,"К кампусу") + controls + panel("Мои мероприятия", '<ul class="rows">'+items+'</ul>' if rows else '<p>Пока нет созданных тобой мероприятий.</p>')
    return page(request, "Организатор мероприятий", body)


def assistant_pages(user):
    if not enabled(user):
        return {}
    staff = user["role"] == "staff"
    result = {
        "lectures": (LECTURES, "Лекции группы", "Эфир и записи, расшифровки, конспекты и домашние работы."),
        "events": (PATH+"/events" if staff else "/activities", "Мероприятия", "Афиша и регистрация, билет и запись в календаре. Организатор управляет публикацией."),
        "coworking": ("/coworking", "Коворкинг", "Полевые замеры загруженности у двери, посещения и ручная сверка."),
        "occupancy": (room_occupancy.PATH, room_occupancy.LABEL, "Демонстрационные данные о занятых и свободных местах."),
        "directory": (campus_directory.PATH, "Люди и подразделения", "Карточки, кабинеты и часы приёма. Свой геостатус можно включить в справочнике."),
    }
    if not room_occupancy.available(user):
        result.pop("occupancy")
    if not campus_directory.available(user):
        result.pop("directory")
    if campus_pay.available(user):
        result["wallet"] = (campus_pay.PATH, "Кампус Pay", "Доступный баланс, покупка за учебные баллы и возврат.")
    return result


def assistant_records(db, user, topic):
    if topic == "wallet":
        balance = campus_pay.amount(db, campus_pay.scope(user))
        return [f"Твой доступный баланс — {balance} демо-баллов."]
    if topic == "coworking":
        from . import coworking
        from .ui import local_label
        row=db.execute("SELECT * FROM field_session WHERE university_id=? AND kind='field' ORDER BY started DESC,rowid DESC LIMIT 1",(user["university_id"],)).fetchone()
        if not row:
            return ["Полевых замеров в коворкинге пока нет."]
        data=coworking.current(db,row)
        last=local_label(data["last"] or row["started"],"Europe/Moscow")+" МСК"
        return [f'{row["room"]}. На {last} последнее показание {data["occupancy"]} из {row["capacity"]} мест. '
                f'Входов {data["enter"]}, выходов {data["exit"]}. Состояние замера: {data["condition"]}.'
                + (" Требуется ручная сверка." if data["check"] else "")]
    if topic == "occupancy":
        from .schedule import local_value
        rooms = room_occupancy.live_values(db, local_value(timestamp(), room_occupancy.ZONE), user["university_id"])
        return [f'Демо · аудитория {v["label"]} · свободно {v["capacity"] - v["count"]} из {v["capacity"]}, '
                f'занято {v["count"]}.' for v in rooms.values()]
    if topic == "directory":
        if user["university_id"] != DEMO:
            return [f"{r['name']} · {r['description']} · {r['office']}" for r in campus_directory.contacts(db,user)]
        return [name + " · " + desc + " · " + campus_map.campus().places[key].label for _,name,desc,key in campus_directory.CONTACTS]
    if topic == "events":
        from . import reservations, staff_management
        if user["role"] == "staff":
            return [r["title"]+" · "+staff_management.RESOURCE_STATES[r["state"]] for r in staff_management.resources(db,user) if r["kind"]=="activity"]
        return [r["title"]+" · "+r["place"] for r in reservations.offers(db,user,kind="activity")]
    from .lecture_channel import visible_lectures
    return [r["title"]+" · "+("запись" if r["ended"] else "в эфире") for r in visible_lectures(db,user)]


def assistant_fallback(user, question):
    import re
    available = assistant_pages(user)
    if not available:
        return None
    q = question.casefold()
    if re.search(r"ко[вл]оркинг",q):
        return {"action":"read_sections","pages":["coworking"]}
    words = {"wallet":r"баланс|кошел[её]|сколько баллов|кампус.?pay",
             "occupancy":r"загруженност|занятост.*аудитор|свободн.*(?:мест|аудитор)",
             "lectures":r"эфир|запис[ьи].*лекци|трансляц", "events":r"мероприяти|афиш",
             "directory":r"справочник|персонали|геозон|геостатус"}
    topics = [name for name, pattern in words.items() if name in available and re.search(pattern,q)]
    return {"action":"read_sections","pages":topics[:3]} if topics else None
