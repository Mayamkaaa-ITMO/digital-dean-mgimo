import hashlib
import json
from uuid import UUID

from .db import write
from .domain import DomainError, field, identifier, require, role, timestamp
from .schedule import edit_time, local_value, staff_lessons

def profile(db, user):
    role(user, "student")
    return require(db.execute("""
        SELECT a.full_name, g.name AS group_name, g.course_year, p.faculty,
               p.name AS program_name, t.label AS semester
        FROM account a
        JOIN study_group g ON g.university_id = a.university_id AND g.id = a.group_id
        JOIN program p ON p.university_id = g.university_id AND p.id = g.program_id
        JOIN academic_period t ON t.university_id = a.university_id AND t.id = a.period_id
        WHERE a.university_id = ? AND a.id = ?
    """, (user["university_id"], user["id"])).fetchone())


def periods(db, user):
    role(user, "student")
    return list(db.execute("""
        SELECT id, label, starts_on FROM academic_period
        WHERE university_id = ? AND (id = ? OR id IN (
            SELECT period_id FROM learning_result WHERE university_id = ? AND student_id = ?
        )) ORDER BY starts_on DESC
    """, (user["university_id"], user["period_id"], user["university_id"], user["id"])))


def results(db, user, period_id=None, result_id=None):
    role(user, "student")
    query = """
        SELECT r.*, s.name AS subject FROM learning_result r
        JOIN subject s ON s.university_id = r.university_id AND s.id = r.subject_id
        WHERE r.university_id = ? AND r.student_id = ?
    """
    params = (user["university_id"], user["id"])
    if result_id is not None:
        return require(db.execute(query + " AND r.id = ?", (*params, result_id)).fetchone())
    return list(db.execute(query + " AND r.period_id = ? ORDER BY s.name",
                           (*params, period_id or user["period_id"])))


def command(db, user, operation, request_id, payload, action):
    try:
        request_id = UUID(request_id).hex
    except (TypeError, ValueError):
        raise DomainError(422, "Открой форму заново и повтори действие.") from None
    fingerprint = hashlib.sha256(json.dumps(payload, ensure_ascii=False).encode()).hexdigest()
    key = (user["university_id"], user["id"], operation, request_id)
    with write(db):
        previous = db.execute("""
            SELECT fingerprint, result_id FROM command
            WHERE university_id = ? AND actor_id = ? AND operation = ? AND request_id = ?
        """, key).fetchone()
        if previous:
            if previous["fingerprint"] != fingerprint:
                raise DomainError(409, "Эта форма уже отправлена с другими данными. Открой её заново.")
            return previous["result_id"]
        result = action()
        db.execute("INSERT INTO command VALUES (?, ?, ?, ?, ?, ?)", (*key, fingerprint, result))
        return result


def notify(db, university_id, student_id, kind, target, key, title, body, now):
    db.execute("""
        INSERT INTO notification
            (id, university_id, student_id, kind, target_id, event_key, title, body, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT (university_id, student_id, event_key) DO NOTHING
    """, (identifier(), university_id, student_id, kind, target, key, title, body, now))


def group_students(db, university, group, period=None):
    return db.execute("""
        SELECT id FROM account WHERE university_id = ? AND group_id = ? AND role = 'student'
           AND (? IS NULL OR period_id = ?)
    """, (university, group, period, period))


def staff_groups(db, user):
    role(user, "staff")
    return list(db.execute("""
        SELECT g.id, g.name FROM staff_group s
        JOIN study_group g ON g.university_id = s.university_id AND g.id = s.group_id
        WHERE s.university_id = ? AND s.staff_id = ? ORDER BY g.name
    """, (user["university_id"], user["id"])))


def publish(db, user, group, title, body, key):
    if group not in {row["id"] for row in staff_groups(db, user)}:
        raise DomainError(403, "У сотрудника нет доступа к этой группе.")
    title = field(title, "Заголовок", 100)
    body = field(body, "Текст", 1500)

    def apply():
        aid, now = identifier(), timestamp()
        university = user["university_id"]
        db.execute("INSERT INTO announcement VALUES (?, ?, ?, ?, ?, ?, ?)",
                   (aid, university, group, user["id"], title, body, now))
        for student in group_students(db, university, group):
            notify(db, university, student["id"], "announcement", aid, f"announcement:{aid}",
                   title, "Объявление деканата", now)
        return aid

    return command(db, user, "announcement.publish", key, [group, title, body], apply)


def announcement(db, user, aid):
    university = user["university_id"]
    if user["role"] == "staff":
        allowed = {row["id"] for row in staff_groups(db, user)}
    else:
        role(user, "student")
        allowed = {user["group_id"]}
    row = db.execute("SELECT * FROM announcement WHERE university_id = ? AND id = ?",
                     (university, aid)).fetchone()
    return require(row if row and row["group_id"] in allowed else None)


def staff_announcements(db, user):
    role(user, "staff")
    return list(db.execute("""
        SELECT a.id, a.title, a.created_at, g.name AS group_name,
               (SELECT count(*) FROM notification n WHERE n.university_id=a.university_id
                AND n.kind='announcement' AND n.target_id=a.id) AS recipients
        FROM announcement a
        JOIN staff_group s ON a.university_id=s.university_id AND a.group_id=s.group_id
        JOIN study_group g ON g.university_id=a.university_id AND g.id=a.group_id
        WHERE a.university_id=? AND s.staff_id=?
        ORDER BY a.created_at DESC, a.rowid DESC LIMIT 50
    """, (user["university_id"], user["id"])))


def move_lesson(db, user, eid, expected_version, starts, ends, room, key):
    event = staff_lessons(db, user, eid)
    try:
        expected_version = int(expected_version)
    except ValueError:
        raise DomainError(422, "Открой актуальную карточку занятия.") from None
    room = field(room, "Аудитория", 80)
    start = edit_time(starts, event["timezone"])
    end = edit_time(ends, event["timezone"])
    if not start < end:
        raise DomainError(422, "Окончание занятия должно быть позже начала.")
    university = user["university_id"]
    period = require(db.execute("""
        SELECT starts_on, ends_on FROM academic_period WHERE university_id = ? AND id = ?
    """, (university, event["period_id"])).fetchone())
    days = [local_value(timestamp(value), event["timezone"]).date().isoformat() for value in (start, end)]
    if not period["starts_on"] <= days[0] <= days[1] <= period["ends_on"]:
        raise DomainError(422, "Выбери дату внутри учебного периода занятия.")
    start, end = timestamp(start), timestamp(end)

    def apply():
        previous = staff_lessons(db, user, eid)
        if previous["version"] != expected_version:
            raise DomainError(409, "Занятие уже изменилось. Открой карточку заново.")
        if (previous["starts_at"], previous["ends_at"], previous["room"]) == (start, end, room):
            return eid
        version = expected_version + 1
        now = timestamp()
        db.execute("INSERT INTO schedule_revision VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                   (university, eid, user["id"], version, previous["starts_at"], previous["ends_at"],
                    previous["room"], now))
        from .campus import room_id
        db.execute("UPDATE schedule_event SET starts_at=?,ends_at=?,room=?,version=?,room_id=? WHERE id=?",
                   (start, end, room, version, room_id(db, university, room), eid))
        new_local = local_value(start, previous["timezone"]).strftime("%d.%m, %H:%M")
        for student in group_students(db, university, previous["group_id"], previous["period_id"]):
            notify(db, university, student["id"], "schedule", eid, f"schedule:{eid}:{version}",
                   "Изменилось расписание", f'{previous["subject"]} · {new_local} · {room}', now)
        return eid

    return command(db, user, "schedule.move", key, [eid, expected_version, start, end, room], apply)


NOTICE_SCOPE = """
    (n.kind NOT IN ('schedule','public_reminder','public_change','announcement') OR
     (?='' AND n.kind='schedule') OR
     (n.kind='announcement' AND EXISTS (
          SELECT 1 FROM announcement a JOIN account u
            ON u.university_id=a.university_id AND u.group_id=a.group_id
          WHERE a.id=n.target_id AND a.university_id=n.university_id AND u.id=n.student_id)) OR
     (n.kind IN ('public_reminder','public_change') AND EXISTS (
         SELECT 1 FROM public_lesson p WHERE p.id=n.target_id AND p.source_id=? AND p.group_id=?)))
"""
NOTICE_FILTERS = {"deadlines": ("exam", "task", "event"), "changes": ("schedule", "public_change")}


def notice_kind_filter(kind):
    if not kind:
        return "", ()
    kinds = NOTICE_FILTERS.get(kind, (kind,))
    return "AND n.kind IN (" + ", ".join("?" for _ in kinds) + ")", kinds


def notice_context(selection):
    source, group = selection or ("", "")
    return source, source, group


def notifications(db, user, unread=False, limit=None, kind="", selection=None, *, offset=0):
    role(user, "student")
    filter_kind, kinds = notice_kind_filter(kind)
    return list(db.execute(f"""
        SELECT n.*, CASE WHEN n.kind='announcement' THEN (
            SELECT a.body FROM announcement a WHERE a.id=n.target_id AND a.university_id=n.university_id
        ) END AS announcement_body
        FROM notification n WHERE university_id = ? AND student_id = ? AND active=1
          AND (? = 0 OR read_at IS NULL) AND {NOTICE_SCOPE} {filter_kind}
          AND (n.kind<>'shared_note' OR EXISTS (
              SELECT 1 FROM team_note s JOIN team t ON t.id=s.team_id AND t.university_id=n.university_id
              JOIN team_member reader ON reader.team_id=t.id AND reader.student_id=n.student_id AND reader.removed_at IS NULL
              JOIN note a ON a.id=s.note_id AND a.university_id=t.university_id
              JOIN team_member owner ON owner.team_id=t.id AND owner.student_id=a.owner_id AND owner.removed_at IS NULL
              WHERE s.note_id=n.target_id AND n.event_key LIKE 'team-note:' || s.team_id || ':%'))
        ORDER BY created_at DESC, rowid DESC LIMIT ? OFFSET ?
    """, (user["university_id"], user["id"], unread, *notice_context(selection), *kinds, limit or -1, offset)))


def mark_read(db, user, nid, selection=None):
    role(user, "student")
    changed = db.execute(f"""
        UPDATE notification AS n SET read_at = COALESCE(read_at, ?)
        WHERE university_id = ? AND student_id = ? AND id = ? AND active=1 AND {NOTICE_SCOPE}
    """, (timestamp(), user["university_id"], user["id"], nid, *notice_context(selection))).rowcount
    if not changed:
        raise DomainError(404, "Уведомление недоступно или не найдено.")


def mark_all_read(db, user, selection=None, kind=""):
    role(user, "student")
    filter_kind, kinds = notice_kind_filter(kind)
    db.execute(f"""
        UPDATE notification AS n SET read_at=?
        WHERE university_id=? AND student_id=? AND active=1 AND read_at IS NULL AND {NOTICE_SCOPE}
          {filter_kind}
    """, (timestamp(), user["university_id"], user["id"], *notice_context(selection), *kinds))
