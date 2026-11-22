from datetime import datetime, timedelta

from . import services as svc, teams, workspace
from .applications import version_check
from .domain import DomainError, field, identifier, require, role, timestamp


def tasks(db, user, tid, task_id=None, *, manage=False):
    parent = teams.team(db, user, tid)
    query = """
        SELECT t.*,x.assignee_id,a.full_name AS assignee,
            (SELECT count(*) FROM subtask s WHERE s.task_id=t.id) AS total,
            (SELECT count(*) FROM subtask s WHERE s.task_id=t.id AND s.done=1) AS done
        FROM team_task x JOIN task t ON t.id=x.task_id LEFT JOIN account a ON a.id=x.assignee_id
        WHERE x.team_id=? AND t.university_id=?
    """
    args = (tid, parent["university_id"])
    if not task_id:
        return list(db.execute(query + " ORDER BY t.state DESC,t.due_at IS NULL,t.due_at,t.created_at", args))
    row = require(db.execute(query + " AND t.id=?", (*args, task_id)).fetchone())
    if manage and user["id"] not in {row["owner_id"], parent["owner_id"], row["assignee_id"]}:
        raise DomainError(403, "Задачу может менять автор, ответственный или владелец команды.")
    return row


def save_task(db, user, tid, inputs, key, task_id=None):
    values = workspace.task_values(db, user, {**inputs, "subject": ""})
    assignee = inputs.get("assignee", "") or None

    def apply():
        parent = teams.team(db, user, tid)
        if assignee and assignee not in {r["id"] for r in teams.members(db, parent)}:
            raise DomainError(422, "Ответственный должен быть действующим участником команды.")
        now = timestamp()
        if task_id:
            previous = tasks(db, user, tid, task_id, manage=True)
            version_check(previous, inputs.get("version"))
            newid = task_id
            db.execute("""
                UPDATE task SET title=?,description=?,subject_id=?,due_at=?,priority=?,version=version+1,updated_at=?
                WHERE id=?
            """, (*values, now, task_id))
            db.execute("UPDATE team_task SET assignee_id=? WHERE task_id=?", (assignee, task_id))
        else:
            newid = workspace.add_task(db, user, values)
            db.execute("INSERT INTO team_task VALUES (?,?,?)", (newid, tid, assignee))
        if assignee and assignee != user["id"]:
            version = db.execute("SELECT version FROM task WHERE id=?", (newid,)).fetchone()[0]
            svc.notify(db, user["university_id"], assignee, "team", tid,
                       f"team-task:{newid}:{version}", "Назначена общая задача", parent["title"], now)
        return newid

    return svc.command(db, user, "team.task.save", key, [tid, task_id, values, assignee, inputs.get("version")], apply)


def task_state(db, user, tid, task_id, state, version, key):
    def apply():
        record = tasks(db, user, tid, task_id, manage=True)
        version_check(record, version)
        db.execute("UPDATE task SET state=?,version=version+1,updated_at=? WHERE id=?",
                   (state, timestamp(), task_id))
        return task_id
    return svc.command(db, user, "team.task.state", key, [tid, task_id, state, version], apply)


def shareable_notes(db, user):
    return list(db.execute("""
        SELECT id,body FROM note WHERE university_id=? AND owner_id=? ORDER BY updated_at DESC LIMIT 30
    """, workspace.scope(user)))


def shared_notes(db, user, tid, nid=None):
    parent = teams.team(db, user, tid)
    query = """
        SELECT n.*,a.full_name FROM team_note g JOIN note n ON n.id=g.note_id
        JOIN account a ON a.id=n.owner_id AND a.university_id=n.university_id
        JOIN team_member m ON m.team_id=g.team_id AND m.student_id=n.owner_id AND m.removed_at IS NULL
        WHERE g.team_id=? AND n.university_id=?
    """
    args = (tid, parent["university_id"])
    if nid:
        return require(db.execute(query + " AND n.id=?", (*args, nid)).fetchone())
    return list(db.execute(query + " ORDER BY g.granted_at DESC", args))


def share(db, user, tid, nid, enabled, key):
    def apply():
        parent = teams.team(db, user, tid)
        workspace.note(db, user, nid)
        if enabled:
            now = timestamp()
            added = db.execute("INSERT INTO team_note VALUES (?,?,?) ON CONFLICT DO NOTHING", (tid, nid, now)).rowcount
            if added:
                for person in teams.members(db, parent):
                    if person["id"] != user["id"]:
                        svc.notify(db, user["university_id"], person["id"], "shared_note", nid,
                                   f"team-note:{tid}:{nid}:{key}", "Открыт доступ к общей заметке", "", now)
        else:
            db.execute("DELETE FROM team_note WHERE team_id=? AND note_id=?", (tid, nid))
            db.execute("""
                UPDATE notification SET active=0 WHERE university_id=? AND kind='shared_note' AND target_id=?
                AND event_key LIKE ?
            """, (parent["university_id"], nid, f"team-note:{tid}:{nid}:%"))
        return tid

    return svc.command(db, user, "team.note.share", key, [tid, nid, enabled], apply)


def post_list(db, user, tid, task_id=None):
    parent = teams.team(db, user, tid)
    if not parent["moderator_id"]:
        return []
    if task_id:
        tasks(db, user, tid, task_id)
    return list(reversed(list(db.execute("""
        SELECT p.*,a.full_name FROM team_message p JOIN account a ON a.id=p.author_id
        WHERE p.team_id=? AND p.task_id IS ? AND p.hidden_at IS NULL ORDER BY p.rowid DESC LIMIT 60
    """, (tid, task_id)))))


def post(db, user, tid, task_id, message, key):
    body = field(message, "Сообщение", 1000)

    def apply():
        parent = teams.team(db, user, tid)
        if not parent["moderator_id"]:
            raise DomainError(409, "Обсуждение откроется, когда будет назначен модератор.")
        if task_id:
            tasks(db, user, tid, task_id)
        now = timestamp()
        since = timestamp(datetime.fromisoformat(now) - timedelta(minutes=1))
        total = db.execute("SELECT count(*) FROM team_message WHERE author_id=? AND created_at>?",
                           (user["id"], since)).fetchone()[0]
        if total >= 5:
            raise DomainError(429, "За минуту можно отправить до пяти сообщений.")
        mid = identifier()
        db.execute("INSERT INTO team_message VALUES (?,?,?,?,?,?,NULL)", (mid, tid, task_id, user["id"], body, now))
        return mid

    return svc.command(db, user, "team.post", key, [tid, task_id, body], apply)


def report(db, user, tid, mid, reason, key):
    reason = field(reason, "Причина жалобы", 300, 3)

    def apply():
        teams.team(db, user, tid)
        target = require(db.execute("SELECT * FROM team_message WHERE id=? AND team_id=? AND hidden_at IS NULL",
                                    (mid, tid)).fetchone())
        if target["author_id"] == user["id"]:
            raise DomainError(422, "Жалоба доступна на сообщение другого участника.")
        db.execute("""
            INSERT INTO team_report (id,message_id,reporter_id,reason,filed_at) VALUES (?,?,?,?,?)
            ON CONFLICT(message_id,reporter_id) DO NOTHING
        """, (identifier(), mid, user["id"], reason, timestamp()))
        return tid
    return svc.command(db, user, "team.report", key, [tid, mid, reason], apply)


def moderation(db, user, rid=None):
    role(user, "staff")
    query = """
        SELECT r.*,p.body,p.team_id,t.title,t.university_id,p.author_id,p.hidden_at
        FROM team_report r JOIN team_message p ON p.id=r.message_id JOIN team t ON t.id=p.team_id
        WHERE t.university_id=? AND t.moderator_id=?
    """
    args = (user["university_id"], user["id"])
    if rid:
        return require(db.execute(query + " AND r.id=?", (*args, rid)).fetchone())
    return list(db.execute(query + " ORDER BY r.resolved_at IS NOT NULL,r.filed_at DESC LIMIT 100", args))


def moderate(db, user, rid, outcome, decision, key):
    decision = field(decision, "Причина решения", 500)

    def apply():
        row = moderation(db, user, rid)
        if row["resolved_at"]:
            raise DomainError(409, "Жалоба уже обработана.")
        now = timestamp()
        if outcome == "hidden":
            db.execute("UPDATE team_message SET hidden_at=? WHERE id=?", (now, row["message_id"]))
        db.execute("""
            UPDATE team_report SET outcome=?,decision=?,resolved_at=?,resolved_by=? WHERE id=?
        """, (outcome, decision, now, user["id"], rid))
        parent = db.execute("SELECT * FROM team WHERE id=?", (row["team_id"],)).fetchone()
        active = {person["id"] for person in teams.members(db, parent)}
        if row["reporter_id"] in active:
            svc.notify(db, user["university_id"], row["reporter_id"], "team", row["team_id"], f"team-report:{rid}",
                       "Жалоба рассмотрена", "Сообщение скрыто." if outcome == "hidden" else "Жалоба отклонена.", now)
        return rid
    return svc.command(db, user, "team.moderate", key, [rid, outcome, decision], apply)
