from datetime import datetime, timedelta

from . import services as svc, workspace
from .domain import DomainError, field, identifier, require, role, timestamp
from .fixtures import demo_id, demo_time
from .schedule import edit_time

MEMBER_SELECT = """
    SELECT a.*,u.timezone,m.allow_availability,m.joined_at
    FROM team_member m JOIN account a ON a.id=m.student_id
    JOIN university u ON u.id=a.university_id
    WHERE m.team_id=? AND a.university_id=? AND m.removed_at IS NULL AND a.role='student'
"""


def seed(db, university, anchor):
    staff = db.execute("SELECT id FROM account WHERE university_id=? AND username='staff' AND role='staff'",
                       (university,)).fetchone()
    if not staff:
        return
    tid = demo_id(university, "club", "applied-science")
    title = "Клуб прикладных экспериментов"
    db.execute("INSERT INTO team VALUES (?,?,?,?,?,'club',?) ON CONFLICT DO NOTHING",
               (tid, university, staff[0], staff[0], title, demo_time(anchor, 10)))
    db.execute("INSERT INTO club VALUES (?,?,?,?) ON CONFLICT DO NOTHING",
               (tid, university, title, "Демонстрационная встреча, обсуждение проектов и разбор экспериментов."))
    starts = anchor + timedelta(days=5)
    mid = demo_id(university, "club", "applied-science-meeting")
    db.execute("""
        INSERT INTO team_meeting (id,team_id,organizer_id,title,starts_at,ends_at) VALUES (?,?,?,?,?,?)
        ON CONFLICT DO NOTHING
    """, (mid, tid, staff[0], "Практикум по командному эксперименту", demo_time(starts, 17), demo_time(starts, 18)))


def lists(db, user):
    return list(db.execute("""
        SELECT t.* FROM team t JOIN team_member m ON m.team_id=t.id
        WHERE t.university_id=? AND m.student_id=? AND m.removed_at IS NULL ORDER BY t.title
    """, workspace.scope(user)))


def team(db, user, tid, *, owning=False):
    query = """
        SELECT t.*,m.allow_availability FROM team t JOIN team_member m ON m.team_id=t.id
        WHERE t.university_id=? AND m.student_id=? AND m.removed_at IS NULL AND t.id=?
    """
    row = require(db.execute(query, (*workspace.scope(user), tid)).fetchone())
    if owning and (row["owner_id"] != user["id"] or row["kind"] != "project"):
        raise DomainError(403, "Управлять составом может владелец проектной команды.")
    return row


def members(db, row):
    return list(db.execute(MEMBER_SELECT + " ORDER BY a.full_name", (row["id"], row["university_id"])))


def join(db, tid, student, now):
    db.execute("""
        INSERT INTO team_member (team_id,student_id,joined_at) VALUES (?,?,?)
        ON CONFLICT(team_id,student_id) DO UPDATE SET joined_at=excluded.joined_at,removed_at=NULL,allow_availability=0
        WHERE team_member.removed_at IS NOT NULL
    """, (tid, student, now))


def create(db, user, title, key):
    uni, owner = workspace.scope(user)
    name = field(title, "Название команды", 90)

    def apply():
        if db.execute("SELECT count(*) FROM team WHERE university_id=? AND owner_id=?", (uni, owner)).fetchone()[0] >= 12:
            raise DomainError(422, "На один аккаунт доступно до 12 команд.")
        moderator = db.execute("""
            SELECT g.staff_id FROM staff_group g JOIN account a ON a.id=g.staff_id AND a.university_id=g.university_id
            WHERE g.university_id=? AND g.group_id=? AND a.role='staff' ORDER BY g.staff_id LIMIT 1
        """, (uni, user["group_id"])).fetchone()
        tid, now = identifier(), timestamp()
        db.execute("INSERT INTO team VALUES (?,?,?,?,?,'project',?)",
                   (tid, uni, owner, moderator[0] if moderator else None, name, now))
        join(db, tid, owner, now)
        return tid

    return svc.command(db, user, "team.create", key, [name], apply)


def invitations(db, user):
    return list(db.execute("""
        SELECT i.*,t.title FROM team_invitation i JOIN team t ON t.id=i.team_id
        WHERE t.university_id=? AND i.student_id=? ORDER BY i.rowid DESC
    """, workspace.scope(user)))


def invitation(db, user, iid):
    return require(next((row for row in invitations(db, user) if row["id"] == iid), None))


def invite(db, user, tid, login, key):
    login = field(login.lower(), "Логин участника", 64)

    def apply():
        row = team(db, user, tid, owning=True)
        uni = row["university_id"]
        target = require(db.execute("""
            SELECT id FROM account WHERE university_id=? AND username=? AND role='student'
        """, (uni, login)).fetchone())[0]
        if target in {member["id"] for member in members(db, row)}:
            raise DomainError(409, "Этот студент уже в команде.")
        now = timestamp()
        db.execute("UPDATE team_invitation SET status='expired' WHERE team_id=? AND status='pending' AND expires_at<=?",
                   (tid, now))
        existing = db.execute("""
            SELECT id FROM team_invitation WHERE team_id=? AND student_id=? AND status='pending'
        """, (tid, target)).fetchone()
        if existing:
            return existing["id"]
        iid = identifier()
        until = timestamp(datetime.fromisoformat(now) + timedelta(days=3))
        db.execute("INSERT INTO team_invitation VALUES (?,?,?,'pending',?,?)", (iid, tid, target, now, until))
        svc.notify(db, uni, target, "team_invite", iid, f"team-invite:{iid}", "Приглашение в команду", row["title"], now)
        return iid

    return svc.command(db, user, "team.invite", key, [tid, login], apply)


def decide_invite(db, user, iid, status, key):
    def apply():
        row = invitation(db, user, iid)
        if row["status"] == status:
            return row["team_id"] if status == "accepted" else iid
        if row["status"] != "pending" or row["expires_at"] <= timestamp():
            raise DomainError(409, "Приглашение уже закрыто или истекло.")
        now = timestamp()
        if status == "accepted":
            count = db.execute("SELECT count(*) FROM team_member WHERE team_id=? AND removed_at IS NULL",
                               (row["team_id"],)).fetchone()[0]
            if count >= 16:
                raise DomainError(409, "В этой команде больше нет мест.")
            join(db, row["team_id"], user["id"], now)
        db.execute("UPDATE team_invitation SET status=? WHERE id=?", (status, iid))
        owner = db.execute("SELECT owner_id FROM team WHERE id=?", (row["team_id"],)).fetchone()[0]
        svc.notify(db, user["university_id"], owner, "team", row["team_id"], f"team-invite:{iid}:{status}",
                   "Ответ на приглашение", "Приглашение принято." if status == "accepted" else "Приглашение отклонено.", now)
        return row["team_id"] if status == "accepted" else iid

    return svc.command(db, user, "team.invitation.response", key, [iid, status], apply)


def revoke_invite(db, user, tid, iid, key):
    def apply():
        row = team(db, user, tid, owning=True)
        invite_row = require(db.execute("SELECT * FROM team_invitation WHERE id=? AND team_id=?", (iid, tid)).fetchone())
        if invite_row["status"] != "pending":
            raise DomainError(409, "Приглашение уже закрыто.")
        db.execute("UPDATE team_invitation SET status='revoked' WHERE id=?", (iid,))
        deactivate(db, row["university_id"], invite_row["student_id"], "team_invite", iid)
        return tid

    return svc.command(db, user, "team.invitation.revoke", key, [tid, iid], apply)


def deactivate(db, university, student, kind, target):
    db.execute("UPDATE notification SET active=0 WHERE university_id=? AND student_id=? AND kind=? AND target_id=?",
               (university, student, kind, target))


def remove(db, user, tid, person, key):
    def apply():
        row = team(db, user, tid, owning=True)
        if person == row["owner_id"]:
            raise DomainError(422, "Владелец остаётся в команде.")
        now = timestamp()
        changed = db.execute("""
            UPDATE team_member SET removed_at=?,allow_availability=0
            WHERE team_id=? AND student_id=? AND removed_at IS NULL
        """, (now, tid, person)).rowcount
        if not changed:
            return tid
        db.execute("UPDATE team_task SET assignee_id=NULL WHERE team_id=? AND assignee_id=?", (tid, person))
        db.execute("""
            UPDATE notification SET active=0 WHERE university_id=? AND kind='shared_note' AND event_key LIKE ?
            AND (student_id=? OR target_id IN (SELECT id FROM note WHERE owner_id=?))
        """, (row["university_id"], f"team-note:{tid}:%", person, person))
        db.execute("DELETE FROM team_note WHERE team_id=? AND note_id IN (SELECT id FROM note WHERE owner_id=?)",
                   (tid, person))
        db.execute("""
            UPDATE team_meeting_response SET status='declined' WHERE student_id=?
            AND meeting_id IN (SELECT id FROM team_meeting WHERE team_id=?)
        """, (person, tid))
        db.execute("""
            UPDATE notification SET active=0 WHERE university_id=? AND student_id=? AND (
                (kind='team' AND target_id=?) OR
                (kind='meeting' AND target_id IN (SELECT id FROM team_meeting WHERE team_id=?)))
        """, (row["university_id"], person, tid, tid))
        return tid

    return svc.command(db, user, "team.member.remove", key, [tid, person], apply)


def consent(db, user, tid, allowed, key):
    def apply():
        team(db, user, tid)
        db.execute("UPDATE team_member SET allow_availability=? WHERE team_id=? AND student_id=?",
                   (allowed, tid, user["id"]))
        return tid
    return svc.command(db, user, "team.availability.consent", key, [tid, allowed], apply)


def club_catalog(db, user):
    university, _ = workspace.scope(user)
    return list(db.execute("SELECT * FROM club WHERE university_id=? ORDER BY title", (university,)))


def approve_club(db, user, row, now):
    from .applications import answers

    target = answers(row).get("_links", {}).get("club", {}).get("id")
    club = require(db.execute("""
        SELECT t.* FROM team t JOIN club c ON c.id=t.id AND c.university_id=t.university_id
        WHERE t.university_id=? AND t.id=? AND t.owner_id=?
    """, (row["university_id"], target, user["id"])).fetchone())
    join(db, club["id"], row["student_id"], now)
    upcoming = db.execute("SELECT id FROM team_meeting WHERE team_id=? AND cancelled=0 AND starts_at>?",
                          (club["id"], now))
    for meeting in upcoming:
        db.execute("INSERT INTO team_meeting_response VALUES (?,?,'pending') ON CONFLICT DO NOTHING",
                   (meeting["id"], row["student_id"]))
    svc.notify(db, row["university_id"], row["student_id"], "team", club["id"], f"club:{row['id']}:joined",
               "Членство в клубе подтверждено", club["title"], now)
