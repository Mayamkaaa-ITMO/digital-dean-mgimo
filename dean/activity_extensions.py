"""Afisha options for the existing booking, campus map and notification records."""
from datetime import timedelta
from functools import cache
from os import getenv
from urllib.parse import urlencode

from . import campus_map, services, workspace
from .domain import DomainError, timestamp
from .schedule import local_value


REMINDER_PREFIX = "activity-reminder:"


def enabled():
    return getenv("DEAN_EXT_EVENTS") == "1"


@cache
def rooms():
    data = campus_map.campus()
    return dict(sorted(
        ((point.key, f"Кампус · {data.location(point.floor)} · {point.label}")
         for point in data.points() if point.kind in {"room", "hall"}
         and data.navigation.route(campus_map.ENTRY, point.key)),
        key=lambda item: item[1],
    ))


@cache
def confirmed_room_labels():
    labels = {}
    for key, label in rooms().items():
        labels[label] = key if label not in labels else None
    return labels


def mapped_room(room):
    if not room or not room.startswith(("Кампус · ", "МГИМО · ")):
        return None
    label = "Кампус · " + room.split(" · ", 1)[1]
    return confirmed_room_labels().get(label)


def lesson_map_url(room, eid, *, context=""):
    key = mapped_room(room)
    if not key:
        return ""
    back = "/schedule/" + eid + ("?" + context if context else "")
    return campus_map.PATH + "?" + urlencode({"room": key, "back": back}) + "#campus-place"


def room_key(db, bid):
    row = db.execute("SELECT place_key FROM activity_room WHERE bookable_id=?", (bid,)).fetchone()
    return row["place_key"] if row else ""


def map_url(db, bid):
    key = room_key(db, bid)
    if key and key in rooms():
        return campus_map.PATH + "?" + urlencode({"from": campus_map.ENTRY, "to": key, "back": "/bookables/" + bid}) + "#campus-plan"
    return ""


def room_label(key, place):
    if not key:
        return place
    if key not in rooms():
        raise DomainError(422, "Выбери аудиторию из списка помещений на карте.")
    return rooms()[key]


def save_room(db, bid, key):
    if key:
        db.execute("""
            INSERT INTO activity_room VALUES (?,?)
            ON CONFLICT(bookable_id) DO UPDATE SET place_key=excluded.place_key
        """, (bid, key))
    else:
        db.execute("DELETE FROM activity_room WHERE bookable_id=?", (bid,))


def next_slots(db, user, until=""):
    """Return the next open slot and its remaining capacity for each activity."""
    rows = db.execute("""
        SELECT s.bookable_id,s.starts_at,s.capacity-count(r.id) AS free
        FROM reservation_slot s JOIN bookable b ON b.id=s.bookable_id AND b.kind='activity'
        LEFT JOIN reservation r ON r.slot_id=s.id AND r.status='reserved'
            AND r.starts_at<s.ends_at AND r.ends_at>s.starts_at
        WHERE s.university_id=? AND s.closed=0 AND s.starts_at>? AND (?='' OR s.starts_at<?)
        GROUP BY s.id ORDER BY s.starts_at,s.id
    """, (user["university_id"], timestamp(), until, until))
    grouped = {}
    for row in rows:
        grouped.setdefault(row["bookable_id"], []).append(row)
    return grouped


def reminder_on(db, rid):
    return bool(db.execute("SELECT 1 FROM activity_reminder WHERE reservation_id=?", (rid,)).fetchone())


def dismiss_reminder(db, rid):
    db.execute("""
        UPDATE notification SET active=0 WHERE kind='booking' AND target_id=? AND event_key LIKE 'activity-reminder:%'
    """, (rid,))


def set_reminder(db, user, rid, active, key):
    from . import reservations

    def apply():
        booking = reservations.get_booking(db, user, rid)
        if booking["kind"] != "activity" or booking["status"] != "reserved" or booking["starts_at"] <= timestamp():
            raise DomainError(409, "Напоминание доступно для предстоящего мероприятия с действующей регистрацией.")
        if active:
            db.execute("INSERT OR IGNORE INTO activity_reminder VALUES (?)", (rid,))
        else:
            db.execute("DELETE FROM activity_reminder WHERE reservation_id=?", (rid,))
            dismiss_reminder(db, rid)
        return rid

    return services.command(db, user, "activity.reminder", key, [rid, active], apply)


def due_reminders(db, user, current):
    if not enabled():
        return
    from .reminders import Reminder

    rows = db.execute("""
        SELECT r.id,r.starts_at,b.title FROM activity_reminder p
        JOIN reservation r ON r.id=p.reservation_id
        JOIN reservation_slot s ON s.id=r.slot_id
        JOIN bookable b ON b.id=s.bookable_id AND b.kind='activity'
        WHERE r.university_id=? AND r.student_id=? AND r.status='reserved'
        AND r.starts_at>? AND r.starts_at<=?
    """, (*workspace.scope(user), timestamp(current), timestamp(current + timedelta(days=1))))
    for row in rows:
        when = local_value(row["starts_at"], user["timezone"]).strftime("%d.%m, %H:%M")
        yield Reminder("booking", row["id"], f"{REMINDER_PREFIX}{row['id']}:{row['starts_at']}",
                       "Скоро мероприятие", f"{row['title']} · {when}")
