from datetime import datetime

from . import workspace
from .clock import now
from .domain import DomainError, identifier, require, role, timestamp
from .services import command


def sessions(db, user):
    role(user, "student")
    return list(db.execute("""
        SELECT * FROM study_timer WHERE owner_id=? ORDER BY state='finished', created_at DESC,id LIMIT 50
    """, (user["id"],)))


def seconds(row):
    if not row["running_since"]:
        return row["seconds"]
    return row["seconds"] + max(0, int((now()-datetime.fromisoformat(row["running_since"])).total_seconds()))


def start(db, user, task_id, key):
    role(user, "student")

    def apply():
        task = workspace.task(db, user, task_id) if task_id else None
        old = db.execute("SELECT id,task_id FROM study_timer WHERE owner_id=? AND state<>'finished'",
                         (user["id"],)).fetchone()
        if old:
            if old["task_id"] != (task_id or None):
                raise DomainError(409, "Заверши текущую сессию перед началом другой.")
            return old["id"]
        stamp = timestamp()
        sid = identifier()
        db.execute("""
            INSERT INTO study_timer(id,owner_id,task_id,task_title,state,created_at,running_since,changed_at)
            VALUES (?,?,?,?,'running',?,?,?)
        """, (sid, user["id"], task_id or None, task["title"] if task else None, stamp, stamp, stamp))
        return sid
    return command(db, user, "study.timer.start", key, [task_id], apply)


def change(db, user, sid, action, version, key):
    role(user, "student")
    transitions = {("running", "pause"): "paused", ("paused", "resume"): "running",
                   ("running", "finish"): "finished", ("paused", "finish"): "finished"}

    def apply():
        row = require(db.execute("SELECT * FROM study_timer WHERE id=? AND owner_id=?", (sid,user["id"])).fetchone())
        state = transitions.get((row["state"], action))
        if str(row["version"]) != version or state is None:
            raise DomainError(409, "Сессия изменилась. Открой историю заново.")
        stamp = timestamp()
        if stamp < row["changed_at"]:
            raise DomainError(409, "Время сервера отстаёт от сохранённого начала.")
        db.execute("""
            UPDATE study_timer SET state=?,seconds=?,running_since=?,changed_at=?,finished_at=?,version=version+1 WHERE id=?
        """, (state, seconds(row), stamp if state=="running" else None, stamp,
              stamp if state=="finished" else None, sid))
        return sid
    return command(db, user, "study.timer." + action, key, [sid, version], apply)
