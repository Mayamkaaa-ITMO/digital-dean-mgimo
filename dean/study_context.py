from datetime import date

from . import public_schedule
from .db import write
from .domain import DomainError, role

DEMO = ("", "")


def parse_selection(value):
    if not value:
        return DEMO
    source, separator, group = value.partition("|")
    if not separator or group not in public_schedule.SOURCES.get(source, {}).get("groups", {}):
        raise DomainError(422, "Выбери опубликованную группу.")
    return source, group


def preference(db, user):
    row = db.execute("SELECT * FROM study_preference WHERE owner_id=?", (user["id"],)).fetchone()
    return ((row["main_source"], row["main_group"]), row["revision"]) if row else (DEMO, 0)


def date_value(value):
    try:
        day = date.fromisoformat(value)
        if 1900 <= day.year <= 9900 and day.isoformat() == value:
            return day
    except (TypeError, ValueError):
        pass
    raise DomainError(422, "Укажи дату в формате ГГГГ-ММ-ДД.")


def option(db, user, selection):
    return db.execute("""
        SELECT favorite,focus_day FROM study_group_option WHERE owner_id=? AND source_id=? AND group_id=?
    """, (user["id"], *(selection or DEMO))).fetchone()


def focus_day(db, user, selection, fallback):
    row = option(db, user, selection)
    return date_value(row["focus_day"]) if row and row["focus_day"] else fallback


def favorites(db, user):
    return [tuple(row) for row in db.execute("""
        SELECT source_id,group_id FROM study_group_option WHERE owner_id=? AND favorite=1 ORDER BY source_id,group_id
    """, (user["id"],)) if row["group_id"] in public_schedule.SOURCES.get(row["source_id"], {}).get("groups", {})]


def change(db, user, selection, action, expected, focus=None):
    role(user, "student")
    if action not in {"view", "main", "return", "favorite", "unfavorite"}:
        raise DomainError(422, "Выбери действие для учебного контекста.")
    day = date_value(focus).isoformat() if focus else None
    with write(db):
        main, revision = preference(db, user)
        if expected and str(revision) != expected:
            raise DomainError(409, "Учебный контекст уже изменён. Открой выбор группы заново.")
        if action == "return":
            selection = parse_selection("|".join(main) if main != DEMO else "")
        previous = option(db, user, selection)
        if action in {"favorite", "unfavorite"}:
            if selection == DEMO:
                raise DomainError(422, "Избранное предназначено для публичных групп.")
            db.execute("""
                INSERT INTO study_group_option(owner_id,source_id,group_id,favorite) VALUES (?,?,?,?)
                ON CONFLICT(owner_id,source_id,group_id) DO UPDATE SET favorite=excluded.favorite
            """, (user["id"], *selection, int(action == "favorite")))
        else:
            public_schedule.set_selection(db, user, *selection)
            if focus is not None and action != "return":
                db.execute("""
                    INSERT INTO study_group_option VALUES (?,?,?,?,?)
                    ON CONFLICT(owner_id,source_id,group_id) DO UPDATE SET focus_day=excluded.focus_day
                """, (user["id"], *selection, previous["favorite"] if previous else 0, day))
                if day:
                    db.execute("""
                        UPDATE calendar_position SET focus_day=? WHERE owner_id=? AND source_id=? AND group_id=?
                    """, (day, user["id"], *selection))
        main = selection if action == "main" else main
        db.execute("""
            INSERT INTO study_preference VALUES (?,?,?,?)
            ON CONFLICT(owner_id) DO UPDATE SET main_source=excluded.main_source,
                main_group=excluded.main_group,revision=excluded.revision
        """, (user["id"], *main, revision + 1))
