"""Resolve room booking requests against actual published windows and the student's calendar."""
from datetime import date, datetime, timedelta
import re
from zoneinfo import ZoneInfo

from . import calendar_tools, public_schedule, reservations
from .agenda import Entry
from .clock import now
from .domain import DomainError, timestamp
from .schedule import edit_time, local_midnight, local_value

DAY_NAMES = {"сегодня": 0, "завтра": 1, "послезавтра": 2, "today": 0, "tomorrow": 1, "day_after": 2}
BOOKING_CATALOG = "/bookings?kind=room"


def command(message, previous=""):
    text = re.sub(r"\s+", " ", message.casefold().strip(" ?!.,"))
    previous = previous.casefold().strip(" ?!.,")
    if text in DAY_NAMES and previous.startswith("забронируй"):
        text = previous + " " + text
    match = re.fullmatch(
        r"забронируй(?:те)?(?: мне)? (.+?)"
        r"(?:(?: на)? (сегодня|завтра|послезавтра|\d{4}-\d{2}-\d{2}|\d{2}\.\d{2}\.\d{4})"
        r"(?: (?:с|в) (\d{1,2}:\d{2})(?: до (\d{1,2}:\d{2}))?)?)?",
        text,
    )
    if not match:
        return None
    room, day, start, end = match.groups()
    if not day and not re.fullmatch(r"(?:помещение|коворкинг|переговорку|переговорную|комнату)(?: [«\"].+[»\"])?", room):
        return None
    return {"action": "book_room", "room": room, "day": day or "", "start": start or "", "end": end or ""}


def query(plan):
    return tuple(plan.get(key, "")[:limit].strip() if isinstance(plan.get(key), str) else ""
                 for key, limit in (("room", 160), ("day", 24), ("start", 8), ("end", 8)))


def matches(room, offer):
    words = re.findall(r"[\w-]+", room.casefold().replace("ё", "е"))
    named = " ".join(offer[key] for key in ("title", "category", "place")).casefold().replace("ё", "е")
    if words == ["коворкинг"]:
        return "коворкинг" in named
    if words in (["переговорную"], ["переговорку"]):
        return "переговор" in named
    words = [w for w in words if w not in {"помещение", "комнату", "комната", "переговорную", "переговорку"}]
    return all(word in named for word in words)


def day_value(day, today):
    day = day.casefold()
    if day in DAY_NAMES:
        return today + timedelta(days=DAY_NAMES[day])
    if re.fullmatch(r"\d{2}\.\d{2}\.\d{4}", day):
        return datetime.strptime(day, "%d.%m.%Y").date()
    return date.fromisoformat(day)


def room_window(db, slot, occupied_calendar, since, until, requested, min_duration):
    if slot["closed"] or slot["ends_at"] <= since or slot["starts_at"] >= until:
        return None
    taken = [Entry(r["id"], "", r["starts_at"], r["ends_at"], "booking", "")
             for r in reservations.occupied(db, slot, since, until)]
    blocked = sorted([*occupied_calendar, *taken], key=lambda event: event.start)
    for low, high in calendar_tools.free_spans(blocked, max(since, slot["starts_at"]), min(until, slot["ends_at"])):
        if requested and (low, high) != (since, until):
            continue
        start, end = datetime.fromisoformat(low), datetime.fromisoformat(high)
        if start.second or start.microsecond:
            start = start.replace(second=0, microsecond=0) + timedelta(minutes=1)
        end = end.replace(second=0, microsecond=0)
        if end - start >= min_duration:
            return start, min(end, start + (min_duration if requested else timedelta(hours=1)))
    return None


def find(db, user, fields):
    room, raw_day, raw_start, raw_end = fields
    if not room:
        return "Какое помещение забронировать? Например, коворкинг или переговорную.", None
    zone = user["timezone"]
    current = now().astimezone(ZoneInfo(zone))
    today = current.date()
    try:
        day = day_value(raw_day, today)
    except ValueError:
        return "На какую дату забронировать помещение? Например, на завтра.", None
    if not today <= day <= today + timedelta(days=31):
        return "Выбери дату в ближайший месяц. День бронирования должен быть сегодня или позже.", None
    upper = day + timedelta(days=1)
    since = max(local_midnight(day, zone), timestamp(current.replace(second=0, microsecond=0) + timedelta(minutes=1)))
    until = local_midnight(upper, zone)
    min_duration = timedelta(minutes=30)
    if raw_start or raw_end:
        try:
            start = edit_time(f"{day.isoformat()}T{raw_start.zfill(5)}", zone)
            finish = (edit_time(f"{day.isoformat()}T{raw_end.zfill(5)}", zone)
                      if raw_end else start + timedelta(hours=1))
            min_duration = finish - start
            if not timedelta() < min_duration <= timedelta(hours=3) or timestamp(start) < since or timestamp(finish) > until:
                raise ValueError
        except (ValueError, DomainError):
            return "Укажи будущий интервал в пределах выбранного дня, до трёх часов. Например, с 12:00 до 13:00.", None
        since, until = timestamp(start), timestamp(finish)

    selection = public_schedule.context(db, user)
    period = calendar_tools.period_for(user, selection)
    first = local_value(since, period.timezone).date()
    last = local_value(until, period.timezone).date() + timedelta(days=1)
    calendar = calendar_tools.calendar(db, user, selection, first, last)
    candidates = []
    for offer in reservations.offers(db, user, kind="room"):
        if not matches(room, offer):
            continue
        for slot in reservations.slots(db, user, offer["id"]):
            interval = room_window(db, slot, calendar, since, until, bool(raw_start), min_duration)
            if interval:
                candidates.append((interval[0], offer["title"], slot["id"], interval[1], offer["place"], slot["version"]))
    if not candidates:
        return f"На {day:%d.%m} подходящего свободного времени не нашлось. Назови другой день или помещение.", None
    start, title, slot_id, end, place, version = min(candidates)
    local_start = local_value(timestamp(start), zone)
    local_end = local_value(timestamp(end), zone)
    caption = f"Забронировать {title} · {local_start:%d.%m в %H:%M}–{local_end:%H:%M}"
    if place:
        caption += f" · {place}"
    return caption, {"slot": slot_id, "version": str(version),
                     "starts": f"{local_start:%Y-%m-%dT%H:%M}", "ends": f"{local_end:%Y-%m-%dT%H:%M}"}
