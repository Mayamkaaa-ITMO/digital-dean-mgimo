"""Private display choices for the Raspay schedule."""

from dataclasses import dataclass
import json
import re

from . import calendar_tools, public_schedule, study_context
from .db import write
from .domain import DomainError, require, role
from .schedule import SQLiteScheduleProvider, student_scope

PALETTES = {
    "lecture": ("Лекция", "#63CDE3", "#365BD7", "#4E43D7", "#54AEA1"),
    "practice": ("Практика", "#EB7539", "#EF994C", "#F2CB4A", "#DC5148"),
    "sport": ("Спорт", "#98CB40", "#53AE83", "#54AEA1", "#F2CB4A"),
    "lab": ("Лабораторная", "#DE4C65", "#D24F95", "#9C53E7", "#DC5148"),
    "research": ("НИР", "#9C53E7", "#875CEB", "#6761E2", "#CB4DDC"),
}
HEX_COLOR = re.compile(r"#[a-fA-F0-9]{6}")


def color_key(kind, subject):
    title = subject.casefold()
    name = kind.casefold()
    if "физкультур" in title or "спорт" in title or name == "спорт":
        return "sport"
    if "лек" in name:
        return "lecture"
    if "лаб" in name:
        return "lab"
    if "прак" in name or "семинар" in name:
        return "practice"
    if "нир" in name or "исследоват" in name:
        return "research"
    return None


@dataclass(frozen=True)
class Preference:
    show_gaps: bool
    colors: dict[str, str]
    room: str
    revision: int

    def color(self, key):
        return self.colors.get(key, PALETTES[key][1]) if key in PALETTES else "#8B8B93"


def context_key(user, selection):
    return user["id"], *(selection or study_context.DEMO)


def preference(db, user, selection):
    row = db.execute("""
        SELECT * FROM schedule_display_preference WHERE owner_id=? AND source_id=? AND group_id=?
    """, context_key(user, selection)).fetchone()
    if row is None:
        return Preference(True, {}, "", 0)
    colors = {key: color for key, color in json.loads(row["colors_json"]).items()
              if key in PALETTES and HEX_COLOR.fullmatch(color)}
    return Preference(bool(row["show_gaps"]), colors, row["language_room"], row["revision"])


def english(subject, subject_id=None, *, demonstration=False):
    return bool(re.search(r"(?:\benglish\b|английск)", subject, re.IGNORECASE)
                or demonstration and subject_id == "english")


def language_rooms(db, user, selection):
    if selection:
        return sorted({label for row in db.execute("""
            SELECT DISTINCT subject,rooms_json FROM public_lesson
            WHERE source_id=? AND group_id=? AND cancelled=0
        """, selection) if english(row["subject"]) for label, _ in json.loads(row["rooms_json"])
                       if label and len(label) <= 120})
    scope = student_scope(user)
    return [row[0] for row in db.execute("""
        SELECT DISTINCT room FROM schedule_event WHERE university_id=? AND group_id=? AND period_id=?
        AND subject_id='english' AND room IS NOT NULL AND length(room) BETWEEN 1 AND 120 ORDER BY room
    """, (scope.university_id, scope.group_id, scope.period_id))]


def update(db, user, selection, action, fields):
    role(user, "student")
    with write(db):
        calendar_tools.verify_context(db, user, selection, fields.get("context_revision"))
        previous = preference(db, user, selection)
        if fields.get("revision") != str(previous.revision):
            raise DomainError(409, "Настройки изменились. Открой их заново.")
        gaps, colors, room = previous.show_gaps, previous.colors.copy(), previous.room
        if action == "gaps":
            value = fields.get("show_gaps")
            if value not in {"0", "1"}:
                raise DomainError(422, "Выбери настройку показа окон.")
            gaps = value == "1"
        elif action == "color":
            key, value = fields.get("kind", ""), fields.get("hex", "")
            if key not in PALETTES or not HEX_COLOR.fullmatch(value):
                raise DomainError(422, "Укажи цвет в формате #RRGGBB.")
            colors[key] = value.upper()
        elif action == "reset-colors":
            colors.clear()
        elif action == "room":
            room = fields.get("room", "")
            if room and room not in language_rooms(db, user, selection):
                raise DomainError(422, "Выбери аудиторию из расписания своей группы.")
        else:
            raise DomainError(422, "Настройка недоступна.")
        db.execute("""
            INSERT INTO schedule_display_preference VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(owner_id,source_id,group_id) DO UPDATE SET show_gaps=excluded.show_gaps,
            colors_json=excluded.colors_json,language_room=excluded.language_room,revision=excluded.revision
        """, (*context_key(user, selection), gaps, json.dumps(colors, sort_keys=True), room, previous.revision + 1))


def lesson(db, user, selection, lesson_id):
    role(user, "student")
    if selection:
        row = public_schedule.local_lesson(db, selection, lesson_id)
        provider = public_schedule.Provider(db, selection)
        return provider.to_lesson(public_schedule.scope(selection), row), row
    provider = SQLiteScheduleProvider(db)
    return require(provider.occurrence(student_scope(user), lesson_id)), None


def hidden_versions(db, user, selection):
    return dict(db.execute("""
        SELECT lesson_id,lesson_version FROM schedule_display_hidden
        WHERE owner_id=? AND source_id=? AND group_id=?
    """, context_key(user, selection)))


def set_hidden(db, user, selection, lesson_id, action, fields):
    role(user, "student")
    with write(db):
        calendar_tools.verify_context(db, user, selection, fields.get("context_revision"))
        event, published = lesson(db, user, selection, lesson_id)
        if str(event.version) != fields.get("source_version"):
            raise DomainError(409, "Пара изменилась. Открой актуальную карточку.")
        key = (*context_key(user, selection), lesson_id)
        if action == "restore":
            db.execute("""
                DELETE FROM schedule_display_hidden WHERE owner_id=? AND source_id=? AND group_id=? AND lesson_id=?
            """, key)
        elif not published or not published["cancelled"]:
            db.execute("""
                INSERT INTO schedule_display_hidden VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(owner_id,source_id,group_id,lesson_id) DO UPDATE
                SET lesson_version=excluded.lesson_version
            """, (*key, event.version))
        return event
