from datetime import date, datetime, time, timedelta
import json
from uuid import NAMESPACE_URL, uuid5
from zoneinfo import ZoneInfo

from . import activity_extensions, retakes, services as svc, workspace
from .applications import version_check
from .domain import DomainError, identifier, require, role, timestamp
from .schedule import edit_time, local_value

KINDS = {"room": "Помещения", "consultation": "Консультации", "sport": "Тренировки", "activity": "Афиша"}
WAIT_KINDS = {"sport", "activity"}
CATALOG = (
    ("room-focus", "room", "Переговорная «Фокус»", "Работа в группе", "ДЕМО · корпус А, 310",
     "Экран, доска, 6 стульев", "Бронь на интервал внутри доступного окна, до трёх часов.", ""),
    ("room-study", "room", "Тихая комната", "Подготовка", "ДЕМО · корпус Б, 105",
     "Стол, 4 места", "В окне возможна одна бронь на выбранный интервал.", ""),
    ("staff-desk", "consultation", "Консультация по учёбе", "Деканат", "ДЕМО · кабинет А-104",
     "", "Одно место на назначенное время.", "Вера Луноречьева"),
    ("sport-volley", "sport", "Открытая игра в волейбол", "Волейбол", "ДЕМО · зал 2",
     "Мячи и сетка", "Место закрепляется после подтверждения в приложении.", "Яна Северова"),
    ("sport-stretch", "sport", "Групповая растяжка", "Растяжка", "ДЕМО · зал 1",
     "Коврики", "Можно отменить запись до начала.", "Илья Меридианов"),
)
ACTIVITIES = (
    ("event-lab", "activity", "Экскурсия в студенческую лабораторию", "Учёба", "ДЕМО · корпус Б, 112",
     "", "Регистрация на демонстрационное мероприятие.", "Олег Полярисов"),
    ("event-talk", "activity", "Встреча проектных команд", "Сообщество", "ДЕМО · аудитория А-210",
     "Экран и проектор", "Число участников ограничено вместимостью.", "Ада Кремнева"),
)
SLOT_BASE = """
    SELECT s.*, CASE WHEN t.id IS NOT NULL THEN 'retake' ELSE b.kind END AS kind,
           b.title,b.category,b.place,b.rules,b.host,b.manager_id
    FROM reservation_slot s JOIN bookable b ON b.id=s.bookable_id AND b.university_id=s.university_id
    LEFT JOIN retake_offer t ON t.id=b.id AND t.university_id=b.university_id
    WHERE s.university_id=?
"""
BOOKING_BASE = """
    SELECT r.*, CASE WHEN t.id IS NOT NULL THEN 'retake' ELSE b.kind END AS kind,
           b.title,b.place,b.id AS bookable_id
    FROM reservation r JOIN reservation_slot s ON s.id=r.slot_id AND s.university_id=r.university_id
    JOIN bookable b ON b.id=s.bookable_id AND b.university_id=r.university_id
    LEFT JOIN retake_offer t ON t.id=b.id AND t.university_id=b.university_id
    WHERE r.university_id=? AND r.student_id=?
"""


def fixture_id(university, key):
    return uuid5(NAMESPACE_URL, f"dean:{university}:reservation:{key}").hex


def seed_reservations(db, university, *, activities=False):
    row = db.execute("""
        SELECT a.id, u.timezone, min(e.starts_at) AS anchor
        FROM account a JOIN university u ON u.id=a.university_id
        JOIN schedule_event e ON e.university_id=u.id
        WHERE a.university_id=? AND a.username='staff' AND a.role='staff'
        GROUP BY a.id,u.timezone
    """, (university,)).fetchone()
    if row is None:
        return
    date = local_value(row["anchor"], row["timezone"]).date()
    zone = ZoneInfo(row["timezone"])
    for index, values in enumerate(ACTIVITIES if activities else CATALOG):
        key, kind, title, category, place, equipment, rules, host = values
        bid = fixture_id(university, key)
        db.execute("""
            INSERT INTO bookable VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO NOTHING
        """, (bid, university, row["id"], kind, title, category, place, equipment, rules, host))
        for pos, offset in enumerate((1, 3, 6)):
            hour = 10 + index
            start = datetime.combine(date + timedelta(days=offset), time(hour), zone)
            finish = start + timedelta(hours=4 if kind == "room" else 1)
            capacity = 1 if kind in {"room", "consultation"} or key == "sport-volley" else 4
            db.execute("""
                INSERT INTO reservation_slot (id, university_id, bookable_id, starts_at, ends_at, capacity)
                VALUES (?, ?, ?, ?, ?, ?) ON CONFLICT(id) DO NOTHING
            """, (fixture_id(university, f"{key}:{pos}"), university, bid, timestamp(start),
                  timestamp(finish), capacity))


def offers(db, user, kind="", category=""):
    query = "SELECT * FROM bookable WHERE university_id=? AND id NOT IN (SELECT id FROM retake_offer)"
    params = (user["university_id"],)
    if user["role"] == "student":
        query += """ AND NOT EXISTS (
            SELECT 1 FROM managed_offer m WHERE m.bookable_id=bookable.id AND (m.state<>'published' OR m.group_id<>?))"""
        params += (user["group_id"],)
    if kind:
        query += " AND kind=?"
        params += (kind,)
    if category:
        query += " AND category=?"
        params += (category,)
    return list(db.execute(query + " ORDER BY kind,title", params))


def offer(db, user, bid, *, manage=False):
    row = require(db.execute("""
        SELECT b.id,b.university_id,b.manager_id,CASE WHEN t.id IS NOT NULL THEN 'retake' ELSE b.kind END AS kind,
               b.title,b.category,b.place,b.equipment,b.rules,b.host
        FROM bookable b LEFT JOIN retake_offer t ON t.id=b.id AND t.university_id=b.university_id
        WHERE b.university_id=? AND b.id=?
    """, (user["university_id"], bid)).fetchone())
    managed = db.execute("SELECT * FROM managed_offer WHERE bookable_id=?",(bid,)).fetchone()
    if managed:
        if manage:
            groups = {entry["id"] for entry in svc.staff_groups(db,user)}
            if managed["group_id"] not in groups:
                raise DomainError(404,"Ресурс недоступен.")
        elif managed["group_id"]!=user["group_id"] or managed["state"]!="published":
            raise DomainError(404,"Ресурс недоступен.")
    if manage:
        role(user, "staff")
        if row["manager_id"] != user["id"]:
            raise DomainError(404, "Ресурс недоступен.")
        if row["kind"] == "retake":
            require(db.execute("""
                SELECT 1 FROM retake_offer t JOIN staff_group g ON g.university_id=t.university_id
                AND g.group_id=t.group_id WHERE t.id=? AND t.university_id=? AND g.staff_id=?
            """, (bid, user["university_id"], user["id"])).fetchone())
    else:
        role(user, "student")
        if row["kind"] == "retake":
            retakes.matching_debt(db, user, bid)
    return row


def slots(db, user, bid, *, upcoming=True, start="", end=""):
    filters = " AND s.bookable_id=?"
    params = (user["university_id"], bid)
    if upcoming:
        filters += " AND s.ends_at>?"
        params += (timestamp(),)
    if start and end:
        filters += " AND s.starts_at<? AND s.ends_at>?"
        params += (end, start)
    return list(db.execute(SLOT_BASE + filters + " ORDER BY s.starts_at", params))


def room_timetable(db, user, room_ids, start, end):
    rooms = {bid: {} for bid in room_ids}
    if not rooms:
        return {}
    rows = db.execute("""
        SELECT s.*, r.id AS reserved_id, r.student_id AS reserved_owner,
               r.starts_at AS reserved_start, r.ends_at AS reserved_end
        FROM reservation_slot s
        LEFT JOIN reservation r ON r.university_id=s.university_id AND r.slot_id=s.id
            AND r.status='reserved' AND r.starts_at<s.ends_at AND r.ends_at>s.starts_at
        WHERE s.university_id=? AND s.bookable_id IN (SELECT value FROM json_each(?))
            AND s.starts_at<? AND s.ends_at>?
        ORDER BY s.bookable_id, s.starts_at, r.starts_at
    """, (user["university_id"], json.dumps(list(rooms)), end, start))
    for row in rows:
        _, taken = rooms[row["bookable_id"]].setdefault(row["id"], (row, []))
        if row["reserved_id"] is not None:
            taken.append({"id": row["reserved_id"], "student_id": row["reserved_owner"],
                          "starts_at": row["reserved_start"], "ends_at": row["reserved_end"]})
    return {bid: list(windows.values()) for bid, windows in rooms.items()}


def slot(db, user, sid, *, manage=False):
    row = require(db.execute(SLOT_BASE + " AND s.id=?", (user["university_id"], sid)).fetchone())
    offer(db, user, row["bookable_id"], manage=manage)
    return row


def is_managed_activity(db, row, bid):
    return row["kind"] == "activity" and bool(db.execute(
        "SELECT 1 FROM managed_offer WHERE bookable_id=?", (bid,)).fetchone())


def occupied(db, row, start=None, end=None):
    start, end = start or row["starts_at"], end or row["ends_at"]
    return list(db.execute("""
        SELECT * FROM reservation WHERE university_id=? AND slot_id=? AND status='reserved'
        AND starts_at<? AND ends_at>? ORDER BY starts_at
    """, (row["university_id"], row["id"], end, start)))


def get_booking(db, user, rid):
    return require(db.execute(BOOKING_BASE + " AND r.id=?", (*workspace.scope(user), rid)).fetchone())


def bookings(db, user, *, kind="", active_only=False, start="", end=""):
    query = BOOKING_BASE
    params = workspace.scope(user)
    if kind:
        query += " AND b.kind=?"
        params += (kind,)
    if active_only:
        query += " AND r.status='reserved'"
    if start and end:
        query += " AND r.starts_at<? AND r.ends_at>?"
        params += (end, start)
    return list(db.execute(query + " ORDER BY r.starts_at", params))


def overlapping_lessons(db, user, starts, ends):
    from . import calendar_tools, public_schedule

    selection = public_schedule.context(db, user)
    zone = calendar_tools.period_for(user, selection).timezone
    first = local_value(starts, zone).date()
    last = local_value(ends, zone).date() + timedelta(days=1)
    return [event for event in calendar_tools.calendar(db, user, selection, first, last)
            if event.kind in {"lesson", "exam"} and event.end and event.start < ends and event.end > starts]


def lesson_conflict(db, user, starts, ends):
    return [event.title for event in overlapping_lessons(db, user, starts, ends)]


def has_conflict(db, user, starts, ends, *, excluding_meeting=None):
    from . import calendar_tools, public_schedule

    excluded = frozenset({f"meeting:{excluding_meeting}"}) if excluding_meeting else frozenset()
    return calendar_tools.busy(db, user, public_schedule.context(db, user), starts, ends, excluding=excluded)


def suggested_room_interval(row, taken, *, start="", end=""):
    current = datetime.fromisoformat(timestamp()).replace(second=0, microsecond=0) + timedelta(minutes=1)
    lower = max(datetime.fromisoformat(row["starts_at"]), current)
    upper = datetime.fromisoformat(row["ends_at"])
    if start and end:
        lower = max(lower, datetime.fromisoformat(start))
        upper = min(upper, datetime.fromisoformat(end))
    reservations = [(datetime.fromisoformat(item["starts_at"]), datetime.fromisoformat(item["ends_at"]))
                    for item in taken]
    starts = sorted({lower, *(finish for _, finish in reservations if lower < finish < upper)})
    for begin in starts:
        finish = min(begin + timedelta(hours=1), upper)
        overlaps = sorted(max(begin, left) for left, right in reservations if left < finish and right > begin)
        if len(overlaps) >= row["capacity"]:
            finish = overlaps[row["capacity"] - 1]
        if begin < finish:
            return begin, finish
    return None


def reservation_times(user, row, raw_start, raw_end):
    if row["kind"] != "room":
        return row["starts_at"], row["ends_at"]
    start = edit_time(raw_start, user["timezone"])
    end = edit_time(raw_end, user["timezone"])
    if not timedelta() < end - start <= timedelta(hours=3):
        raise DomainError(422, "Бронь помещения должна длиться больше нуля и до трёх часов.")
    return timestamp(start), timestamp(end)


def reserve(db, user, sid, raw_start, raw_end, acknowledge, key, version, *, check_calendar=False):
    destination = slot(db, user, sid)
    start, end = reservation_times(user, destination, raw_start, raw_end)
    university, owner = workspace.scope(user)

    def apply():
        row = slot(db, user, sid)
        version_check(row, version)
        if row["closed"] or start <= timestamp() or not row["starts_at"] <= start < end <= row["ends_at"]:
            raise DomainError(409, "Проверь время. Запись доступна на открытый будущий интервал внутри окна.")
        existing = occupied(db, row, start, end)
        duplicate = next((r for r in existing if r["student_id"] == owner
                          and (r["starts_at"], r["ends_at"]) == (start, end)), None)
        if duplicate:
            return duplicate["id"]
        if len(existing) >= row["capacity"]:
            raise DomainError(409, "Свободного места на этот интервал уже нет.")
        debt = retakes.check_new_attempt(db, user, row["bookable_id"], start, end) if row["kind"] == "retake" else None
        if bookings(db, user, active_only=True, start=start, end=end):
            raise DomainError(409, "У тебя уже есть запись на пересекающийся интервал.")
        if check_calendar:
            if has_conflict(db, user, start, end):
                raise DomainError(409, "В календаре уже есть событие на это время. Попроси Вишку подобрать другой интервал.")
        elif lesson_conflict(db, user, start, end) and not acknowledge:
            raise DomainError(422, "Запись пересекается с учебной парой. Подтверди, что хочешь записаться.")
        rid = save_reservation(db, row, owner, start, end)
        if debt:
            db.execute("INSERT INTO retake_booking (reservation_id,debt_id) VALUES (?,?)", (rid, debt))
        return rid

    payload = [sid, raw_start, raw_end, acknowledge]
    if check_calendar:
        payload.append("check_calendar")
    return svc.command(db, user, "reservation.book", key, payload, apply)


def save_reservation(db, slot, owner, start, end, *, automatic=False):
    rid, stamp = identifier(), timestamp()
    university, sid = slot["university_id"], slot["id"]
    db.execute("""
        INSERT INTO reservation (id,university_id,student_id,slot_id,starts_at,ends_at,status,created_at)
        VALUES (?,?,?,?,?,?,'reserved',?)
    """, (rid, university, owner, sid, start, end, stamp))
    db.execute("""
        UPDATE reservation_wait SET status='left',auto_from=NULL,auto_to=NULL,auto_until=NULL
        WHERE university_id=? AND student_id=? AND slot_id=?
    """, (university, owner, sid))
    title = "Место подтверждено автоматически" if automatic else "Демонстрационная запись подтверждена"
    svc.notify(db, university, owner, "booking", rid, f"booking:{rid}:reserved", title, slot["title"], stamp)
    return rid


def alert_queue(db, row, places=1):
    if row["kind"] not in WAIT_KINDS or row["closed"] or row["starts_at"] <= timestamp():
        return
    empty = row["capacity"] - len(occupied(db, row))
    if empty <= 0:
        return
    candidates = list(db.execute("""
        SELECT * FROM reservation_wait WHERE university_id=? AND slot_id=? AND status='waiting'
        ORDER BY queued_at,rowid LIMIT ?
    """, (row["university_id"], row["id"], min(places, empty))))
    for candidate in candidates:
        if row["kind"] == "sport" and candidate["auto_until"] and timestamp() < candidate["auto_until"]:
            user = db.execute("""
                SELECT a.*,u.timezone FROM account a JOIN university u ON u.id=a.university_id
                WHERE a.university_id=? AND a.id=? AND a.role='student'
            """, (row["university_id"], candidate["student_id"])).fetchone()
            if user:
                start, end = row["starts_at"], row["ends_at"]
                day = local_value(start, user["timezone"]).date().isoformat()
                if (candidate["auto_from"] <= day <= candidate["auto_to"]
                        and (candidate["auto_start"], candidate["auto_end"]) == (start, end)
                        and not has_conflict(db, user, start, end)
                        and len(occupied(db, row)) < row["capacity"]):
                    save_reservation(db, row, user["id"], start, end, automatic=True)
                    continue
        db.execute("UPDATE reservation_wait SET status='notified' WHERE id=?", (candidate["id"],))
        svc.notify(db, row["university_id"], candidate["student_id"], "activity", row["id"],
                   f"wait:{candidate['id']}:{candidate['attempt']}", "Место могло освободиться",
                   row["title"] + " · Для подтверждения открой запись.", timestamp())


def cancel(db, user, rid, key):
    def apply():
        row = get_booking(db, user, rid)
        if row["status"] == "cancelled":
            return rid
        if row["starts_at"] <= timestamp():
            raise DomainError(409, "Начавшуюся запись отменить нельзя.")
        if row["kind"] == "activity" and activity_extensions.enabled() and db.execute(
                "SELECT 1 FROM reservation_attendance WHERE reservation_id=?", (rid,)).fetchone():
            raise DomainError(409, "Посещение уже подтверждено. Запись отменить нельзя.")
        stamp = timestamp()
        db.execute("UPDATE reservation SET status='cancelled',cancelled_at=? WHERE id=?", (stamp, rid))
        if row["kind"] == "activity":
            activity_extensions.dismiss_reminder(db, rid)
        svc.notify(db, *workspace.scope(user), "booking", rid, f"booking:{rid}:cancelled",
                   "Демонстрационная запись отменена", row["title"], stamp)
        alert_queue(db, slot(db, user, row["slot_id"]))
        return rid

    return svc.command(db, user, "reservation.cancel", key, [rid], apply)


def wait_record(db, user, sid):
    return db.execute("""
        SELECT * FROM reservation_wait WHERE university_id=? AND student_id=? AND slot_id=?
    """, (*workspace.scope(user), sid)).fetchone()


def waiting(db, user, sid):
    row = wait_record(db, user, sid)
    return row["status"] if row else None


def wait(db, user, sid, action, key, inputs=None):
    university, owner = workspace.scope(user)

    def apply():
        row = slot(db, user, sid)
        if row["kind"] not in WAIT_KINDS:
            raise DomainError(422, "Лист ожидания доступен для тренировок и мероприятий.")
        if action == "leave":
            db.execute("""UPDATE reservation_wait SET status='left',auto_from=NULL,auto_to=NULL,auto_until=NULL
                WHERE university_id=? AND student_id=? AND slot_id=?""",
                       (university, owner, sid))
            return sid
        if action == "withdraw-auto":
            db.execute("""UPDATE reservation_wait SET auto_from=NULL,auto_to=NULL,auto_until=NULL
                WHERE university_id=? AND student_id=? AND slot_id=?""", (university, owner, sid))
            return sid
        if row["closed"] or row["starts_at"] <= timestamp():
            raise DomainError(409, "Лист ожидания закрыт.")
        booked = occupied(db, row)
        if any(x["student_id"] == owner for x in booked):
            raise DomainError(409, "У тебя уже есть подтверждённая запись.")
        if len(booked) < row["capacity"]:
            raise DomainError(409, "Место доступно. Запишись через форму подтверждения.")
        db.execute("""
            INSERT INTO reservation_wait (id,university_id,student_id,slot_id,status,queued_at,attempt)
            VALUES (?, ?, ?, ?, 'waiting', ?, 1)
            ON CONFLICT(university_id,student_id,slot_id) DO UPDATE SET
            status='waiting',queued_at=excluded.queued_at,attempt=attempt+1,auto_from=NULL,auto_to=NULL,auto_until=NULL
            WHERE reservation_wait.status<>'waiting'
        """, (identifier(), university, owner, sid, timestamp()))
        if action == "auto-wait":
            if row["kind"] != "sport" or inputs.get("consent") != "1":
                raise DomainError(422, "Для автозаписи нужно явно подтвердить согласие.")
            try:
                first = date.fromisoformat(inputs.get("from", ""))
                last = date.fromisoformat(inputs.get("to", ""))
            except ValueError:
                raise DomainError(422, "Укажи даты действия согласия.") from None
            until = timestamp(edit_time(inputs.get("until", ""), user["timezone"]))
            day = local_value(row["starts_at"], user["timezone"]).date()
            if (not first <= day <= last or last - first > timedelta(days=31)
                    or not timestamp() < until <= row["starts_at"]):
                raise DomainError(422, "Диапазон до 31 дня должен включать тренировку. Согласие действует до начала или меньше.")
            db.execute("""UPDATE reservation_wait SET auto_from=?,auto_to=?,auto_until=?,auto_start=?,auto_end=?
                WHERE university_id=? AND student_id=? AND slot_id=?""",
                       (first.isoformat(), last.isoformat(), until, row["starts_at"], row["ends_at"], university, owner, sid))
        return sid

    terms = [inputs.get(name) for name in ("consent", "from", "to", "until")] if action == "auto-wait" else []
    return svc.command(db, user, "reservation.wait", key, [sid, action, *terms], apply)


def participants(db, user, sid):
    row = slot(db, user, sid, manage=True)
    return list(db.execute("""
        SELECT a.full_name,r.id,r.starts_at,r.ends_at,v.confirmed_at FROM reservation r JOIN account a ON a.id=r.student_id
        AND a.university_id=r.university_id
        LEFT JOIN reservation_attendance v ON v.reservation_id=r.id
        WHERE r.university_id=? AND r.slot_id=? AND r.status='reserved' ORDER BY r.starts_at,a.full_name
    """, (row["university_id"], sid)))


def record_attendance(db, user, window, row):
    rid = row["id"]
    now = timestamp()
    changed = db.execute("INSERT INTO reservation_attendance VALUES (?,?,?) ON CONFLICT DO NOTHING",
                         (rid, user["id"], now)).rowcount
    if changed:
        svc.notify(db, window["university_id"], row["student_id"], "booking", rid, f"booking:{rid}:attended",
                   "Посещение подтверждено", window["title"], now)
        if window["kind"] == "activity":
            from .achievements import activity_award
            activity_award(db, user, window, row, now)
    return bool(changed)


def confirm_attendance(db, user, sid, rid, key):
    def apply():
        window = slot(db, user, sid, manage=True)
        row = require(db.execute("""
            SELECT * FROM reservation WHERE university_id=? AND slot_id=? AND id=? AND status='reserved'
        """, (window["university_id"], sid, rid)).fetchone())
        if window["kind"] not in {"sport", "activity"} or row["ends_at"] > timestamp():
            raise DomainError(409, "Подтвердить посещение можно после завершения записи.")
        record_attendance(db,user,window,row)
        return rid

    return svc.command(db, user, "reservation.attended", key, [sid, rid], apply)


def sport_visits(db, user):
    recorded = {row[0] for row in db.execute("""
        SELECT a.reservation_id FROM reservation_attendance a JOIN reservation r ON r.id=a.reservation_id
        WHERE r.university_id=? AND r.student_id=? AND r.status='reserved'
    """, workspace.scope(user))}
    return [row for row in bookings(db, user, kind="sport") if row["id"] in recorded]


def update_slot(db, user, target, inputs, key, *, creating=False):
    record = offer(db, user, target, manage=True) if creating else slot(db, user, target, manage=True)
    bid = record["id"] if creating else record["bookable_id"]
    zone = user["timezone"]
    start = timestamp(edit_time(inputs.get("starts", ""), zone))
    end = timestamp(edit_time(inputs.get("ends", ""), zone))
    capacity = inputs.get("capacity", "")
    if not capacity.isascii() or not capacity.isdecimal() or len(capacity) > 4 or not 1 <= int(capacity) <= 1000:
        raise DomainError(422, "Укажи вместимость от 1 до 1000.")
    capacity = int(capacity)
    if record["kind"] in {"room", "consultation"} and capacity != 1:
        raise DomainError(422, "В этом окне допустима одна одновременная запись.")
    closed = int(inputs.get("closed") == "1")
    university = user["university_id"]
    if start >= end or end <= timestamp():
        raise DomainError(422, "Конец открываемого окна должен быть в будущем и позже его начала.")

    def apply():
        item = offer(db, user, bid, manage=True)
        if activity_extensions.enabled() and is_managed_activity(db, item, bid):
            raise DomainError(409, "Измени окно в карточке управления мероприятием.")
        sid = identifier() if creating else target
        overlaps = db.execute("""
            SELECT 1 FROM reservation_slot WHERE university_id=? AND bookable_id=? AND id<>?
            AND starts_at<? AND ends_at>? LIMIT 1
        """, (university, bid, sid, end, start)).fetchone()
        if overlaps:
            raise DomainError(409, "Другое окно этого ресурса пересекается по времени.")
        if creating:
            db.execute("""
                INSERT INTO reservation_slot (id,university_id,bookable_id,starts_at,ends_at,capacity,closed)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (sid, university, bid, start, end, capacity, closed))
        else:
            row = slot(db, user, sid, manage=True)
            version_check(row, inputs.get("version"))
            existing = occupied(db, row)
            if existing and (
                any(not start <= r["starts_at"] < r["ends_at"] <= end for r in existing)
                or (row["kind"] != "room" and (start != row["starts_at"] or end != row["ends_at"]))
                or (row["kind"] != "room" and capacity < len(existing))
            ):
                raise DomainError(409, "Изменение нарушит уже подтверждённую запись.")
            db.execute("""
                UPDATE reservation_slot SET starts_at=?,ends_at=?,capacity=?,closed=?,version=version+1 WHERE id=?
            """, (start, end, capacity, closed, sid))
            released = capacity - (0 if row["closed"] else row["capacity"])
            if released > 0:
                alert_queue(db, slot(db, user, sid, manage=True), released)
        return sid

    return svc.command(db, user, "reservation.slot", key, [target, creating, start, end, capacity, closed], apply)
