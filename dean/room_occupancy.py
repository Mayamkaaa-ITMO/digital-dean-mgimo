"""Demo room counts, spatial density estimates and historical forecasts."""
from datetime import timedelta
import json
from os import getenv
from random import Random
from statistics import median
from urllib.parse import urlencode

from . import activity_extensions, campus_map, reservations, services
from .clock import now
from .domain import DomainError, timestamp
from .event_tickets import DEMO
from .schedule import local_value
from .ui import back, button, e, empty, form, hidden, link, local_label, page, panel, select_field

PATH = "/campus-occupancy"
LABEL = "Загруженность аудиторий"
ZONE = "Europe/Moscow"
CAPACITIES = {"b1-1012": 32, "b2-2018a": 48, "n4-423": 96}
DEMO_COUNTS = {"b1-1012": 9, "b2-2018a": 27, "n4-423": 78}
ZONE_WEIGHTS = {
    "b1-1012": (5, 9, 4, 3, 6, 2, 3, 5, 2),
    "b2-2018a": (2, 3, 4, 3, 6, 8, 4, 6, 8),
    "n4-423": (3, 5, 6, 6, 8, 9, 2, 5, 7),
}
ZONE_ROWS = ("Первые ряды", "Средние ряды", "Задние ряды")
ZONE_COLUMNS = ("слева", "в центре", "справа")
PERIODS = {"day": ("24 часа", 24), "week": ("7 дней", 168)}
PARTS = (("До 08:00", 0, 8), ("08:00–12:00", 8, 12), ("12:00–17:00", 12, 17), ("После 17:00", 17, 24))
LEVELS = (("empty", "0%"), ("low", "1–39%"), ("medium", "40–74%"), ("high", "От 75%"))
PROFILE = {8: .27, 9: .67, 10: .85, 11: .69, 12: .34, 13: .66, 14: .83,
           15: .57, 16: .73, 17: .32, 18: .42, 19: .24, 20: .11}


def available(user):
    return bool(user and (getenv("DEAN_PRODUCT") == "1"
                or (user["university_id"] == DEMO and getenv("DEAN_EXT_ROOM_OCCUPANCY") == "1")))


def require_demo(user):
    if not available(user):
        raise DomainError(404, "Демонстрационные данные аудиторий недоступны.")


def samples(cutoff):
    for room, capacity in CAPACITIES.items():
        people = 0
        for offset in range(14 * 24, -1, -1):
            moment = cutoff - timedelta(hours=offset)
            local = local_value(timestamp(moment), ZONE)
            noise = Random(f"{room}:{local.isoformat()}")
            share = PROFILE.get(local.hour, 0) * (1 if local.weekday() < 5 else .43)
            target = min(capacity, round(capacity * share * noise.uniform(.65, 1.25)))
            turnover = min(people, target, noise.randrange(4))
            yield room, timestamp(moment), max(0, target - people) + turnover, max(0, people - target) + turnover
            people = target


def populate(db, user, key):
    require_demo(user)

    def apply():
        saved = db.execute("SELECT max(observed_at) FROM demo_room_flow").fetchone()[0]
        if saved:
            return saved
        current = local_value(timestamp(now()), ZONE)
        cutoff = current.replace(hour=14, minute=0, second=0, microsecond=0)
        if cutoff > current:
            cutoff -= timedelta(days=1)
        db.executemany("INSERT INTO demo_room_flow VALUES (?,?,?,?)", samples(cutoff))
        return timestamp(cutoff)

    return services.command(db, user, "room-occupancy.synthetic", key, [], apply)


def dataset(db, period):
    if period not in PERIODS:
        raise DomainError(422, "Выбери период 24 часа или 7 дней.")
    cutoff = db.execute("SELECT max(observed_at) FROM demo_room_flow").fetchone()[0]
    data = {"schema_version": "1.0", "source": "synthetic", "timezone": ZONE,
            "period": period, "updated_at": cutoff, "rooms": []}
    if not cutoff:
        return data
    data_map = campus_map.campus()
    cutoff_date = local_value(cutoff, ZONE)
    low = timestamp(cutoff_date - timedelta(hours=PERIODS[period][1]))
    target = cutoff_date + timedelta(hours=1)
    data["start_exclusive"] = low
    for key, capacity in CAPACITIES.items():
        rows = db.execute("""
            SELECT observed_at,entered,exited FROM demo_room_flow WHERE room_key=? ORDER BY observed_at
        """, (key,))
        people, window, historical = 0, [], []
        for stamp, entered, exited in rows:
            people += entered - exited
            observed = local_value(stamp, ZONE)
            if stamp > low:
                window.append((observed.hour, people, entered, exited))
            if observed.hour == target.hour and (observed.weekday() < 5) == (target.weekday() < 5):
                historical.append(people)
        point = data_map.places[key]
        cells = []
        for label, start, end in PARTS:
            counts = [count for hour, count, _, _ in window if start <= hour < end]
            cells.append({"label": label, "peak": max(counts) if counts else None})
        data["rooms"].append({
            "room_key": key, "label": point.label, "floor": point.floor, "capacity": capacity,
            "latest": people, "peak": max(count for _, count, _, _ in window),
            "average": round(sum(count for _, count, _, _ in window) / len(window), 1),
            "entered": sum(entered for _, _, entered, _ in window), "exited": sum(exited for _, _, _, exited in window),
            "parts": cells, "map_url": campus_map.PATH + "?" + urlencode({"to": key}) + "#campus-plan",
            "forecast": {"at": timestamp(target), "value": round(median(historical)) if historical else None,
                         "method": "historical_hour_median", "samples": len(historical)},
        })
    return data


def load_level(share):
    if share >= 75:
        return "high"
    if share >= 40:
        return "medium"
    return "low" if share else "empty"


def heat_cell(value, capacity):
    if value is None:
        return '<td>—</td>'
    share = round(100 * value / capacity)
    return f'<td><span data-heat="{load_level(share)}">{share}%</span></td>'


def index(request, db):
    require_demo(request.user)
    period = request.query("period", "day")
    report = dataset(db, period)
    if request.query("format") == "json":
        return 200, json.dumps(report, ensure_ascii=False), [("Content-Type", "application/json; charset=utf-8")]
    if request.user["role"] == "staff":
        home = back("/campus-lab", "К кампусу") if getenv("DEAN_CAMPUS_LAB") == "1" else back("/staff", "В кабинет")
    else:
        home = back("/all-services", "К сервисам")
    body = (home
            + '<p class="occupancy-demo-note"><strong>Демо</strong> '
              'Количество людей и распределение по залу показаны на учебных данных.</p>')
    if request.user["role"] == "student":
        body += link("/bookings?kind=room", "Свободное время для бронирования", "dean-secondary-action")
    body += live_heat(request, db, period)
    data = report["rooms"]
    if not data:
        action = form(request, PATH, button("Создать учебную историю"), command=True)
        body += panel("История загрузки", empty("Подготовь учебные показания, чтобы посмотреть пики загрузки.") + action)
        return page(request, LABEL, body, section="campus-occupancy")
    controls = select_field("period", "Период", [(key, value[0]) for key, value in PERIODS.items()], period)
    updated = local_label(report["updated_at"], ZONE)
    headers = "".join(f'<th scope="col">{e(name)}</th>' for name, _, _ in PARTS)
    matrix, history_rows = [], []
    for room in data:
        cells = "".join(heat_cell(cell["peak"], room["capacity"]) for cell in room["parts"])
        matrix.append(f'<tr><th scope="row">{e(room["label"])}</th>{cells}</tr>')
        forecast = room["forecast"]
        shown = f'{forecast["value"]} чел.' if forecast["value"] is not None else "Мало наблюдений"
        history_rows.append(f'<li><strong>{e(room["label"])}</strong><span>Пик {room["peak"]} · среднее {room["average"]} · '
                            f'входов {room["entered"]} · выходов {room["exited"]}</span>'
                            f'<span>Прогноз на {e(local_label(forecast["at"], ZONE))} МСК · {shown} · '
                            f'наблюдений {forecast["samples"]}</span></li>')
    table = ('<div class="occupancy-table" role="region" aria-label="История загруженности по аудиториям" tabindex="0">'
             '<table><caption>Пиковая доля занятых мест</caption>'
             f'<thead><tr><th scope="col">Аудитория</th>{headers}</tr></thead><tbody>{"".join(matrix)}</tbody></table></div>')
    history = (f'<form class="inline" method="get" action="{PATH}#occupancy-history">{controls}{button("Показать")}</form>'
               f'<p class="meta">Последнее наблюдение в учебном наборе · {e(updated)} МСК</p>{table}'
               f'<details class="occupancy-history-details"><summary>Показатели за период</summary><ul class="rows">'
               f'{"".join(history_rows)}</ul><p class="meta">Прогноз по учебной истории использует медиану '
               'показаний за тот же час в будни или выходные.</p></details>')
    body += f'<section id="occupancy-history">{panel("История загрузки", history)}</section>'
    return page(request, LABEL, body, section="campus-occupancy")


def past_counts(db, room):
    count = 0
    history = []
    for row in db.execute("SELECT observed_at,entered,exited FROM demo_room_flow WHERE room_key=? ORDER BY observed_at", (room,)):
        count += row["entered"] - row["exited"]
        history.append((local_value(row["observed_at"], ZONE), count))
    return history


def measured_hours(db, room, university_id):
    # Use the final observed counter in each hour that had an actual signal.
    hourly = {}
    for row in db.execute("""
        SELECT recorded,occupied FROM lab_door WHERE university_id=? AND room=? ORDER BY recorded,id
    """, (university_id, room)):
        hour = local_value(row["recorded"],ZONE).replace(minute=0,second=0,microsecond=0)
        hourly[hour] = row["occupied"]
    return list(hourly.items())


def last_signal(db, room, university_id):
    return db.execute("""
        SELECT occupied,recorded FROM lab_door WHERE university_id=? AND room=? ORDER BY id DESC LIMIT 1
    """, (university_id, room)).fetchone()


def nearest_hour(history, at, current, capacity, measured=()):
    """k-nearest historical transitions, weighted by hour, weekday and starting load."""
    examples = []
    for observations, weight in ((history,1.0),(measured,2.0)):
        for (t, n), (later, target) in zip(observations, observations[1:]):
            if later - t != timedelta(hours=1) or later > at:
                continue
            gap = abs(at.hour - t.hour)
            distance = (min(gap, 24-gap)/3)**2 + ((n-current)/capacity*3)**2
            distance += 2 * ((at.weekday() < 5) != (t.weekday() < 5))
            examples.append((distance, -t.timestamp(), target, weight))
    neighbors = sorted(examples)[:7]
    if not neighbors:
        return None
    weights = [w/(0.1+d) for d, _, _, w in neighbors]
    return round(sum(w*row[2] for w,row in zip(weights,neighbors))/sum(weights))


def door(request, db):
    from . import campus_lab
    campus_lab.access(request.user)
    require_demo(request.user)
    if request.user["role"] != "staff":
        raise DomainError(403, "Счётчиком управляет сотрудник.")
    room, change = request.field("room"), request.field("direction")
    if room not in CAPACITIES or change not in {"enter", "exit"}:
        raise DomainError(422, "Выбери аудиторию и направление.")
    delta = 1 if change == "enter" else -1

    def record():
        university_id = request.user["university_id"]
        last = last_signal(db, room, university_id)
        occupied = last["occupied"] if last else DEMO_COUNTS[room]
        if not 0 <= occupied + delta <= CAPACITIES[room]:
            raise DomainError(409, "Показание выходит за пределы вместимости.")
        return str(db.execute("INSERT INTO lab_door(university_id,room,recorded,delta,occupied) VALUES (?,?,?,?,?)",
                              (university_id, room, timestamp(), delta, occupied + delta)).lastrowid)

    services.command(db, request.user, "lab.door", request.field("request_id"), [room,change], record)
    if request.env.get("HTTP_ACCEPT")=="application/json":
        return 200, '{"counted":true}', [("Content-Type","application/json")]
    period = "?period=week" if request.field("period") == "week" else ""
    return 303, b"", [("Location", f"{PATH}{period}#occupancy-{room}")]


def demo_zones(room, count):
    weights = ZONE_WEIGHTS[room]
    size, extra = divmod(CAPACITIES[room], len(weights))
    capacities = [size + (index < extra) for index in range(len(weights))]
    counts = [0] * len(weights)
    # Allocate the next person to the most underrepresented available zone.
    for _ in range(count):
        available = (index for index, load in enumerate(counts) if load < capacities[index])
        index = min(available, key=lambda index: (counts[index] + 1) / weights[index])
        counts[index] += 1
    return [
        {"label": f"{ZONE_ROWS[index // 3]}, {ZONE_COLUMNS[index % 3]}",
         "count": load, "capacity": capacity, "level": load_level(round(100 * load / capacity))}
        for index, (load, capacity) in enumerate(zip(counts, capacities))
    ]


def live_values(db, at, university_id):
    values = {}
    campus = campus_map.campus()
    for room, capacity in CAPACITIES.items():
        history = past_counts(db, room)
        last = last_signal(db, room, university_id)
        if last:
            count, recorded_at = last["occupied"], last["recorded"]
            source_kind = "demo_signal"
            source = "Сигнал учебного счётчика"
        else:
            count, recorded_at = DEMO_COUNTS[room], None
            source_kind = "synthetic"
            source = "Учебный снимок"
        pct = round(100 * count / capacity)
        measured = measured_hours(db, room, university_id)
        values[room] = {"label": campus.places[room].label, "count": count, "capacity": capacity, "pct": pct,
                        "level": load_level(pct), "source_kind": source_kind,
                        "prediction": nearest_hour(history, at, count, capacity, measured),
                        "source": f"{source} · {local_label(recorded_at, ZONE)} МСК" if recorded_at else source,
                        "recorded_at": recorded_at, "zones": demo_zones(room, count)}
    return values


def latest(request,db):
    require_demo(request.user)
    values = live_values(db, local_value(timestamp(), ZONE), request.user["university_id"])
    return 200,json.dumps(values,ensure_ascii=False),[("Content-Type","application/json; charset=utf-8"),("Cache-Control","no-store")]


def floor_heat(campus, key):
    from .campus_map_view import display_labels
    point=campus.places[key]
    floor=campus.floors[point.floor]
    match=next((item for item in display_labels(campus,floor) if item[0]==key),None)
    if not match or match[3] is None:
        return ""
    idx=match[3]
    left,top,width,height=floor.areas[idx][2]
    x,y=left+width/2,top+height/2
    w,h=max(300,width*2.3),max(180,height*2.3)
    shapes="".join(f'<path class="{"lab-active-room" if i==idx else "lab-other-room"}" d="{e(path)}"/>' for i,(_,path,_) in enumerate(floor.areas))
    return (f'<svg class="lab-floor" viewBox="{x-w/2:g} {y-h/2:g} {w:g} {h:g}" role="img" aria-label="Загруженность аудитории {e(point.label)} на плане этажа">'
            f'{shapes}<path class="lab-walls" d="{e(floor.walls)}"/><text x="{x:g}" y="{y:g}">{e(point.label)}</text></svg>')


def zone_map(zones):
    cells = []
    for index, zone in enumerate(zones):
        title = f'{zone["label"]} · {zone["count"]} из {zone["capacity"]} мест'
        cells.append(f'<li data-zone="{index}" data-level="{zone["level"]}" '
                     f'aria-label="{e(title)}" title="{e(title)}">{zone["count"]}</li>')
    return ('<div class="occupancy-zones"><p>Доска</p>'
            f'<ol role="list" aria-label="Учебное распределение по залу">{"".join(cells)}</ol></div>')


def live_heat(request, db, period):
    campus = campus_map.campus()
    cells = []
    values = live_values(db, local_value(timestamp(), ZONE), request.user["university_id"])
    controls = request.user["role"] == "staff" and getenv("DEAN_CAMPUS_LAB") == "1"
    booking_links = {}
    if request.user["role"] == "student":
        for offer in reservations.offers(db, request.user, "room"):
            key = activity_extensions.room_key(db, offer["id"]) or activity_extensions.mapped_room(offer["place"])
            if key in CAPACITIES:
                booking_links.setdefault(key, f'/bookables/{offer["id"]}')
    for room, capacity in CAPACITIES.items():
        current = values[room]
        count, predicted, level = current["count"], current["prediction"], current["level"]
        point = campus.places[room]
        marker = current["source"]
        origin = PATH + "?" + urlencode({"period": period})
        url = campus_map.PATH + "?" + urlencode({"to": room, "back": origin}) + "#campus-plan"
        action = ""
        if controls:
            fields = hidden("room", room) + hidden("period", period)
            for key, label in (("enter", "Добавить вход"), ("exit", "Добавить выход")):
                action += form(request, PATH + "/door", fields + hidden("direction", key) + button(label), command=True)
            action = f'<details><summary>Сымитировать проход</summary><div class="lab-controls">{action}</div></details>'
        forecast = f"Учебный прогноз через час · {predicted} чел." if predicted is not None else "Прогноз пока недоступен"
        floor_plan = floor_heat(campus, room)
        floor_detail = f'<details><summary>На плане этажа</summary>{floor_plan}</details>' if floor_plan else ""
        actions = link(url, "Маршрут на карте", "dean-secondary-action")
        if room in booking_links:
            actions += link(booking_links[room], "Время для брони", "dean-secondary-action")
        cells.append(f'<article class="lab-heat" id="occupancy-{room}" data-room="{room}" data-level="{level}">'
                     f'<header class="occupancy-room-heading"><div><h3>{e(point.label)}</h3>'
                     f'<p>{e(campus.location(point.floor))}</p></div><strong data-room-share>{current["pct"]}%</strong></header>'
                     f'<dl class="occupancy-room-seats"><div><dt>Занято мест</dt><dd data-room-count>{count} из {capacity}</dd></div>'
                     f'<div><dt>Свободно мест</dt><dd data-room-free>{capacity - count}</dd></div></dl>'
                     f'<meter min="0" max="{capacity}" value="{count}" aria-label="Занято мест в помещении {e(point.label)}"></meter>'
                     f'{zone_map(current["zones"])}<div class="occupancy-room-actions">{actions}</div>{floor_detail}{action}'
                     '<details><summary>Источник и прогноз</summary><p class="occupancy-room-meta">'
                     f'<span data-room-source>{e(marker)}</span><span data-room-forecast>{forecast}</span></p>'
                     '</details></article>')
    camera = ""
    if controls and getenv("DEAN_ROOM_OCCUPANCY_CAMERA") == "1":
        from .static_assets import asset_url
        request.allow_door_camera = True
        options = [(key, campus.places[key].label) for key in CAPACITIES]
        camera = (f'<details class="lab-camera" data-door-camera data-csrf="{e(request.csrf)}"><summary>Камера у двери</summary>'
                  '<p>Камера запускается по кнопке и добавляет сигналы к учебному счётчику. '
                  'Для отдельного замера у двери открой <a href="/coworking">счётчик коворкинга</a>.</p>'
                  +select_field("room","Аудитория",options,next(iter(CAPACITIES)))+
                  '<label class="check"><input type="checkbox">Вход справа налево</label>'
                  '<div class="lab-camera-view"><video muted autoplay playsinline></video></div>'
                  '<p>Кадры обрабатываются на устройстве. В журнал отправляются только аудитория и направление прохода.</p>'
                  '<button type="button" data-camera-start>Включить камеру</button> <button type="button" disabled data-camera-stop>Остановить камеру</button>'
                  '<p role="status"></p></details>'+f'<script type="module" src="{asset_url("door-counter.js")}"></script>')
    legend = "".join(f'<li data-level="{level}">{label}</li>' for level, label in LEVELS)
    busiest = max(values, key=lambda room: values[room]["count"])
    highlighted = values[busiest]
    hide = " hidden" if not highlighted["count"] else ""
    most = (f'<p class="occupancy-busiest" data-busiest{hide}>Больше всего людей — '
            f'<a href="#occupancy-{busiest}">{e(highlighted["label"])}</a> · '
            f'<strong>{highlighted["count"]} чел.</strong></p>')
    return ('<section id="door-flow" aria-labelledby="occupancy-map-title"><h2 id="occupancy-map-title">Тепловая карта</h2>'
            '<p>Число в ячейке показывает условное распределение по рядам. Цвет показывает долю занятых мест.</p>'
            f'<ul class="occupancy-legend" aria-label="Шкала загруженности">{legend}</ul>'
            f'<div class="lab-grid occupancy-room-grid">{"".join(cells)}</div>{most}{camera}</section>')
