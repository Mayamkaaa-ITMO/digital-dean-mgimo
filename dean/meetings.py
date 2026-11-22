from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from . import services as svc, teams, workspace
from .applications import version_check
from .domain import DomainError, field, identifier, require, timestamp
from .schedule import edit_time, local_value, today_for

STATES = {"pending": "Ожидает ответа", "accepted": "Принято", "declined": "Отклонено"}


def upcoming(db, user, tid):
    teams.team(db, user, tid)
    return list(db.execute("""
        SELECT m.*,r.status FROM team_meeting m JOIN team_meeting_response r ON r.meeting_id=m.id AND r.student_id=?
        WHERE m.team_id=? ORDER BY m.starts_at DESC LIMIT 30
    """, (user["id"], tid)))


def meeting(db, user, mid):
    university, actor = workspace.scope(user)
    return require(db.execute("""
        SELECT m.*,t.title AS team_name,r.status FROM team_meeting m JOIN team t ON t.id=m.team_id
        JOIN team_member member ON member.team_id=t.id AND member.student_id=? AND member.removed_at IS NULL
        JOIN team_meeting_response r ON r.meeting_id=m.id AND r.student_id=member.student_id
        WHERE t.university_id=? AND m.id=?
    """, (actor, university, mid)).fetchone())


def calendar(db, user, since, until, *, excluding=None):
    university, actor = workspace.scope(user)
    return list(db.execute("""
        SELECT m.*,t.title AS team_name FROM team_meeting m JOIN team t ON t.id=m.team_id
        JOIN team_meeting_response r ON r.meeting_id=m.id
        JOIN team_member a ON a.team_id=t.id AND a.student_id=r.student_id AND a.removed_at IS NULL
        WHERE t.university_id=? AND r.student_id=? AND r.status='accepted' AND m.cancelled=0
        AND m.starts_at<? AND m.ends_at>? AND m.id IS NOT ? ORDER BY m.starts_at
    """, (university, actor, until, since, excluding)))


def permitted(db, user, tid):
    parent = teams.team(db, user, tid)
    people = consenting_members(db, parent)
    if not parent["allow_availability"] or len(people) < 2:
        raise DomainError(422, "Для подбора дай согласие сам и дождись согласия ещё одного участника.")
    return parent, people


def consenting_members(db, parent):
    return [row for row in teams.members(db, parent) if row["allow_availability"]]


def suggestions(db, user, tid, first, last, minutes):
    from .agenda import entries
    from .schedule import SQLiteScheduleProvider

    _, people = permitted(db, user, tid)
    try:
        first_day, last_day = date.fromisoformat(first), date.fromisoformat(last)
        duration = int(minutes)
    except ValueError:
        raise DomainError(422, "Укажи даты и длительность встречи.") from None
    if (not today_for(user) <= first_day <= last_day <= today_for(user) + timedelta(days=31)
            or (last_day - first_day).days > 6 or duration not in {30, 60, 90, 120}):
        raise DomainError(422, "Выбери до семи дней в ближайший месяц и длительность от 30 до 120 минут.")
    busy = []
    after = last_day + timedelta(days=1)
    for person in people:
        for item in entries(db, person, SQLiteScheduleProvider(db), first_day, after):
            if item.end or item.kind == "exam":
                busy.append((item.start, item.end))
    duration = timedelta(minutes=duration)
    zone = ZoneInfo(user["timezone"])
    options, day = [], first_day
    while day <= last_day and len(options) < 8:
        start = datetime.combine(day, time(9), zone)
        limit = start + timedelta(hours=11)
        while start + duration <= limit and len(options) < 8:
            ends = start + duration
            begin, end = timestamp(start), timestamp(ends)
            overlaps = any(observed < end and (finish > begin if finish else observed >= begin)
                           for observed, finish in busy)
            if begin > timestamp() and not overlaps:
                options.append((begin, end))
            start += timedelta(minutes=30)
        day += timedelta(days=1)
    return len(people), options


def history(db, user, mid, action, start, end, now):
    db.execute("""
        INSERT INTO team_meeting_history (meeting_id,actor_id,action,starts_at,ends_at,created_at)
        VALUES (?,?,?,?,?,?)
    """, (mid, user["id"], action, start, end, now))


def save(db, user, tid, inputs, key, mid=None):
    from .reservations import has_conflict

    title = field(inputs.get("title", ""), "Название подготовки", 100)
    start = edit_time(inputs.get("start", ""), user["timezone"])
    end = edit_time(inputs.get("end", ""), user["timezone"])
    if not timedelta(minutes=30) <= end - start <= timedelta(hours=2):
        raise DomainError(422, "Выбери интервал от 30 до 120 минут.")
    starts, ends = timestamp(start), timestamp(end)

    def apply():
        parent, people = permitted(db, user, tid)
        now = timestamp()
        if starts <= now or start > datetime.fromisoformat(now) + timedelta(days=31):
            raise DomainError(422, "Выбери время в ближайший месяц.")
        if mid:
            old = meeting(db, user, mid)
            version_check(old, inputs.get("version"))
            if old["team_id"] != tid or old["organizer_id"] != user["id"] or old["cancelled"]:
                raise DomainError(403, "Перенос доступен организатору действующей встречи.")
        if any(has_conflict(db, person, starts, ends, excluding_meeting=mid) for person in people):
            raise DomainError(409, "У одного из участников интервал занят. Подбери другое время.")
        meeting_id = mid or identifier()
        if mid:
            version = old["version"] + 1
            db.execute("""
                UPDATE team_meeting SET title=?,starts_at=?,ends_at=?,version=? WHERE id=?
            """, (title, starts, ends, version, mid))
            db.execute("DELETE FROM team_meeting_response WHERE meeting_id=?", (mid,))
            db.execute("UPDATE notification SET active=0 WHERE university_id=? AND kind='meeting' AND target_id=?",
                       (parent["university_id"], mid))
        else:
            version = 1
            db.execute("""
                INSERT INTO team_meeting (id,team_id,organizer_id,title,starts_at,ends_at) VALUES (?,?,?,?,?,?)
            """, (meeting_id, tid, user["id"], title, starts, ends))
        history(db, user, meeting_id, "moved" if mid else "created", starts, ends, now)
        for person in people:
            response = "accepted" if person["id"] == user["id"] else "pending"
            db.execute("INSERT INTO team_meeting_response VALUES (?,?,?)", (meeting_id, person["id"], response))
            if response == "pending":
                label = "Совместная встреча перенесена" if mid else "Приглашение на совместную встречу"
                svc.notify(db, parent["university_id"], person["id"], "meeting", meeting_id,
                           f"meeting:{meeting_id}:{version}", label, parent["title"], now)
        return meeting_id

    return svc.command(db, user, "meeting.save", key, [tid, mid, title, starts, ends, inputs.get("version")], apply)


def respond(db, user, mid, state, version, key):
    from .reservations import has_conflict

    def apply():
        row = meeting(db, user, mid)
        version_check(row, version)
        if row["cancelled"] or row["starts_at"] <= timestamp():
            raise DomainError(409, "Изменение ответа на эту встречу недоступно.")
        if row["status"] == state:
            return mid
        if state == "accepted" and has_conflict(db, user, row["starts_at"], row["ends_at"], excluding_meeting=mid):
            raise DomainError(409, "Интервал теперь занят. Проверь своё расписание.")
        db.execute("UPDATE team_meeting_response SET status=? WHERE meeting_id=? AND student_id=?",
                   (state, mid, user["id"]))
        if row["organizer_id"] != user["id"]:
            history(db, user, mid, state, row["starts_at"], row["ends_at"], timestamp())
            organizer = db.execute("SELECT role FROM account WHERE id=?", (row["organizer_id"],)).fetchone()
            if organizer[0] == "student":
                svc.notify(db, user["university_id"], row["organizer_id"], "meeting", mid,
                           f"meeting-response:{mid}:{user['id']}:{key}", "Ответ на приглашение", STATES[state], timestamp())
        return mid
    return svc.command(db, user, "meeting.respond", key, [mid, state, version], apply)


def cancel(db, user, mid, version, key):
    def apply():
        row = meeting(db, user, mid)
        version_check(row, version)
        if row["organizer_id"] != user["id"]:
            raise DomainError(403, "Отменить встречу может организатор.")
        if row["cancelled"]:
            return mid
        if row["starts_at"] <= timestamp():
            raise DomainError(409, "Встреча уже началась.")
        now = timestamp()
        db.execute("UPDATE team_meeting SET cancelled=1,version=version+1 WHERE id=?", (mid,))
        history(db, user, mid, "cancelled", row["starts_at"], row["ends_at"], now)
        for person in db.execute("""
            SELECT r.student_id FROM team_meeting_response r JOIN team_member a ON a.student_id=r.student_id
            AND a.team_id=? AND a.removed_at IS NULL WHERE r.meeting_id=? AND r.student_id<>?
        """, (row["team_id"], mid, user["id"])):
            svc.notify(db, user["university_id"], person["student_id"], "meeting", mid,
                       f"meeting:{mid}:cancelled", "Совместная встреча отменена", row["team_name"], now)
        return mid
    return svc.command(db, user, "meeting.cancel", key, [mid, version], apply)
