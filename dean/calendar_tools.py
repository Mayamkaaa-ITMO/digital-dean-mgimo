"""Owner-scoped calendar options, private overrides, and local availability."""

from datetime import datetime, time, timedelta
from urllib.parse import urlencode
from zoneinfo import ZoneInfo

from . import public_schedule, study_context
from .db import write
from .domain import DomainError, field, role, timestamp
from .schedule import SQLiteScheduleProvider, edit_time, local_midnight, local_value, student_scope
from .services import command

VISIBILITY = {"all": "Учёба и личные дела", "study": "Только учебные события", "personal": "Личные дела и брони"}
STUDY = frozenset({"lesson", "exam"})
SCHEDULE_VIEWS = frozenset({"day", "week", "list", "month"})


def schedule_query(day, view, kind="", subject="", visibility="all"):
    return urlencode({"date": day.isoformat(), "view": view, "kind": kind,
                      "subject": subject, "visibility": visibility})


def lesson_context(query, day=None):
    from .agenda import TYPES

    view = query("view", "")
    if view not in SCHEDULE_VIEWS:
        return ""
    try:
        focus = study_context.date_value(query("date", ""))
    except DomainError:
        if day is None:
            return ""
        focus = day
    kind, subject, visibility = (query(key, "") for key in ("kind", "subject", "visibility"))
    return schedule_query(focus, view, kind if kind in TYPES else "", subject[:240],
                          visibility if visibility in VISIBILITY else "all")


def lesson_schedule(request, day, default_view):
    context = lesson_context(request.query, day)
    return "/schedule?" + (context or schedule_query(day, default_view))


def position(db, user, selection, fallback):
    row = db.execute("""
        SELECT focus_day,view FROM calendar_position WHERE owner_id=? AND source_id=? AND group_id=?
    """, (user["id"], *(selection or study_context.DEMO))).fetchone()
    if row:
        return study_context.date_value(row["focus_day"]), row["view"]
    return study_context.focus_day(db, user, selection, fallback), "week"


def remember_position(db, user, selection, focus, view):
    db.execute("""
        INSERT INTO calendar_position VALUES (?,?,?,?,?)
        ON CONFLICT(owner_id,source_id,group_id) DO UPDATE SET focus_day=excluded.focus_day,view=excluded.view
    """, (user["id"], *(selection or study_context.DEMO), focus.isoformat(), view))


def filters(db, user, selection):
    row = db.execute("""
        SELECT kind,subject,visibility FROM calendar_filter WHERE owner_id=? AND source_id=? AND group_id=?
    """, (user["id"], *(selection or study_context.DEMO))).fetchone()
    return tuple(row) if row else ("", "", "all")


def save_filters(db, user, selection, revision, kind, subject, visibility):
    from .agenda import TYPES
    from . import personal_study, workspace

    role(user, "student")
    valid = (personal_study.suggestions(db, selection) if selection
             else {row["id"] for row in workspace.subjects(db, user)})
    if kind not in {"", *TYPES} or subject and subject not in valid or visibility not in VISIBILITY:
        raise DomainError(422, "Выбери допустимые фильтры календаря.")
    with write(db):
        verify_context(db, user, selection, revision)
        db.execute("""
            INSERT INTO calendar_filter VALUES (?,?,?,?,?,?)
            ON CONFLICT(owner_id,source_id,group_id) DO UPDATE SET kind=excluded.kind,
                subject=excluded.subject,visibility=excluded.visibility
        """, (user["id"], *(selection or study_context.DEMO), kind, subject, visibility))


def verify_context(db, user, selection, expected):
    if (public_schedule.context(db, user) != selection
            or str(study_context.preference(db, user)[1]) != expected):
        raise DomainError(409, "Контекст изменился. Пересчитай результат в выбранной группе.")


def follow(db, user, selection, enabled, revision):
    role(user, "student")
    if not selection:
        raise DomainError(422, "Выбери публичную группу.")
    with write(db):
        verify_context(db, user, selection, revision)
        key = (user["id"], *selection)
        if enabled:
            db.execute("INSERT OR IGNORE INTO public_group_follow VALUES (?,?,?)", key)
        else:
            db.execute("DELETE FROM public_group_follow WHERE owner_id=? AND source_id=? AND group_id=?", key)


def is_followed(db, user, selection):
    return db.execute("SELECT 1 FROM public_group_follow WHERE owner_id=? AND source_id=? AND group_id=?",
                      (user["id"], *selection)).fetchone() is not None


def override(db, user, lesson_id):
    return db.execute("SELECT * FROM calendar_adjustment WHERE owner_id=? AND lesson_id=?",
                      (user["id"], lesson_id)).fetchone()


def set_override(db, user, selection, lesson_id, mode, fields, key):
    role(user, "student")

    def apply():
        if "context_revision" in fields:
            verify_context(db, user, selection, fields["context_revision"])
        elif public_schedule.context(db, user) != selection:
            raise DomainError(409, "Группа уже изменилась.")
        source = public_schedule.local_lesson(db, selection, lesson_id)
        previous = override(db, user, lesson_id)
        revision = previous["revision"] if previous else 0
        if str(source["version"]) != fields.get("source_version") or str(revision) != fields.get("revision"):
            raise DomainError(409, "Событие или поправка изменились. Сверь новую версию.")
        if mode == "remove":
            db.execute("DELETE FROM calendar_adjustment WHERE owner_id=? AND lesson_id=?", (user["id"], lesson_id))
        else:
            if source["cancelled"]:
                raise DomainError(409, "Источник отменил пару. Удали поправку или создай личное событие.")
            zone = public_schedule.scope(selection).timezone
            start = edit_time(fields.get("starts", ""), zone)
            end = edit_time(fields.get("ends", ""), zone)
            if not timedelta(0) < end - start <= timedelta(days=1):
                raise DomainError(422, "Поправка должна задавать интервал до одного дня.")
            room = field(fields.get("room", ""), "Аудитория", 120, 0)
            db.execute("""
                INSERT INTO calendar_adjustment VALUES (?,?,?,?,?,?,?)
                ON CONFLICT(owner_id,lesson_id) DO UPDATE SET starts_at=excluded.starts_at,ends_at=excluded.ends_at,
                    room=excluded.room,base_version=excluded.base_version,revision=excluded.revision
            """, (user["id"], lesson_id, timestamp(start), timestamp(end), room, source["version"], revision + 1))
        return lesson_id

    return command(db, user, "calendar.override." + mode, key, [lesson_id, fields, selection], apply)


def public_events(db, owner, selection, since, until):
    return db.execute("""
        SELECT e.*,w.observed_at,a.starts_at AS local_start,a.ends_at AS local_end,
               a.room AS local_room,a.base_version
        FROM public_lesson e JOIN public_week w USING(source_id,group_id,monday)
        LEFT JOIN calendar_adjustment a ON a.lesson_id=e.id AND a.owner_id=?
        WHERE e.source_id=? AND e.group_id=? AND e.cancelled=0 AND (
            (e.starts_at < ? AND e.ends_at > ?) OR
            (a.base_version=e.version AND a.starts_at < ? AND a.ends_at > ?)
        )
    """, (owner, *selection, until, since, until, since))


def visible(kind, visibility):
    return visibility == "all" or (visibility == "study") == (kind in STUDY)


def calendar(db, user, selection, first, stop):
    from .agenda import entries
    provider = public_schedule.Provider(db, selection) if selection else SQLiteScheduleProvider(db)
    return entries(db, user, provider, first, stop, selection)


def period_for(user, selection):
    return public_schedule.scope(selection) if selection else student_scope(user)


def intersects(items, start, end, *, visibility="all", excluding=frozenset()):
    return any(item.key not in excluding and visible(item.kind, visibility)
               and item.end and item.start < end and item.end > start for item in items)


def busy(db, user, selection, start, end, *, visibility="all", excluding=frozenset()):
    zone = period_for(user, selection).timezone
    first, last = local_value(start, zone).date(), local_value(end, zone).date()
    items = calendar(db, user, selection, first, last + timedelta(days=1))
    return intersects(items, start, end, visibility=visibility, excluding=excluding)


def free_spans(items, start, end, *, visibility="all"):
    cursor = start
    for item in items:
        if not item.end or not visible(item.kind, visibility) or item.end <= cursor or item.start >= end:
            continue
        if cursor < item.start:
            yield cursor, item.start
        cursor = max(cursor, item.end)
        if cursor >= end:
            return
    if cursor < end:
        yield cursor, end


def minute_value(raw):
    try:
        value = time.fromisoformat(raw)
        if value.strftime("%H:%M") == raw:
            return value
    except (TypeError, ValueError):
        pass
    raise DomainError(422, "Укажи часы в формате ЧЧ:ММ.")


def window(first, last, raw_start, raw_end, visibility, *, max_days=31):
    first, last = study_context.date_value(first), study_context.date_value(last)
    start, end = minute_value(raw_start), minute_value(raw_end)
    if not 0 <= (last-first).days < max_days or end <= start or visibility not in VISIBILITY:
        raise DomainError(422, f"Выбери корректные часы и диапазон до {max_days} дней.")
    return first, last, start, end


def day_boundary(day, hour, zone):
    return timestamp(datetime.combine(day, hour, ZoneInfo(zone)))


def free_windows(items, first, last, start_hour, end_hour, zone, visibility):
    for index in range((last-first).days + 1):
        day = first + timedelta(days=index)
        start = day_boundary(day, start_hour, zone)
        end = day_boundary(day, end_hour, zone)
        for low, high in free_spans(items, start, end, visibility=visibility):
            yield day, low, high


def slot_time(raw):
    try:
        value = datetime.fromisoformat(raw)
        if raw == timestamp(value) and value.tzinfo is not None and 1900 <= value.year <= 9900:
            return value
    except (TypeError, ValueError, OverflowError):
        pass
    raise DomainError(422, "Интервал недействителен. Пересчитай свободное время.")


def book_free(db, user, selection, fields, key):
    from .workspace import add_event

    role(user, "student")
    start, end = fields.get("starts"), fields.get("ends")
    minutes = slot_time(end) - slot_time(start)
    mode = fields.get("visibility")
    title = field(fields.get("title", ""), "Название", 120)
    if not timedelta(minutes=15) <= minutes <= timedelta(hours=12) or mode not in VISIBILITY:
        raise DomainError(422, "Выбери интервал продолжительностью от 15 минут до 12 часов.")

    def apply():
        verify_context(db, user, selection, fields.get("context_revision"))
        if busy(db, user, selection, start, end, visibility="all"):
            raise DomainError(409, "Интервал занят по общему календарю. Пересчитай свободное время.")
        return add_event(db, user, (title, "", "", start, end, 0, None))

    return command(db, user, "calendar.free", key, [selection, start, end, title, mode, fields.get("context_revision")], apply)
