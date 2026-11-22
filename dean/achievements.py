import json

from . import portfolio, services as svc
from .domain import DomainError, field, identifier, require, role, timestamp
from .pdf import render_document
from .workspace import scope


def review_state(db, eid):
    return db.execute("SELECT * FROM achievement_review WHERE entry_id=?", (eid,)).fetchone()


def request_review(db, user, eid, key, *, withdraw=False):
    def apply():
        row = portfolio.entry(db, user, eid)
        if row["kind"] != "achievement" or row["verified_at"]:
            raise DomainError(409, "Подтверждение доступно для личного неподтверждённого достижения.")
        existing = review_state(db, eid)
        if withdraw:
            if existing and existing["state"] == "pending":
                db.execute("DELETE FROM achievement_review WHERE entry_id=?", (eid,))
            return eid
        db.execute("""
            INSERT INTO achievement_review (entry_id,state,version,requested_at) VALUES (?,'pending',?,?)
            ON CONFLICT(entry_id) DO UPDATE SET state='pending',requested_at=excluded.requested_at,
            version=excluded.version,reviewer_id=NULL,feedback=NULL,reviewed_at=NULL
            WHERE achievement_review.state='refused'
        """, (eid, row["version"], timestamp()))
        return eid
    return svc.command(db, user, "achievement.request", key, [eid, withdraw], apply)


def requests(db, user, eid=None):
    role(user, "staff")
    query = """
        SELECT r.*,e.title,e.content,e.university_id,e.student_id,e.version AS current_version,a.full_name
        FROM achievement_review r JOIN portfolio_entry e ON e.id=r.entry_id
        JOIN account a ON a.id=e.student_id AND a.university_id=e.university_id
        JOIN staff_group g ON g.university_id=a.university_id AND g.group_id=a.group_id
        WHERE e.university_id=? AND g.staff_id=?
    """
    args = (user["university_id"], user["id"])
    if eid:
        return require(db.execute(query + " AND e.id=?", (*args, eid)).fetchone())
    return list(db.execute(query + " ORDER BY r.state<>'pending',r.requested_at DESC LIMIT 100", args))


def decide(db, user, eid, state, feedback, specific_request, key):
    if state not in {"accepted", "refused"}:
        raise DomainError(422, "Выбери решение по достижению.")
    note = field(feedback, "Основание", 400)

    def apply():
        row = requests(db, user, eid)
        if row["state"] != "pending" or row["current_version"] != row["version"] or row["requested_at"] != specific_request:
            raise DomainError(409, "Запрос изменился. Открой актуальные достижения.")
        now = timestamp()
        db.execute("""
            UPDATE achievement_review SET state=?,reviewer_id=?,feedback=?,reviewed_at=? WHERE entry_id=?
        """, (state, user["id"], note, now, eid))
        if state == "accepted":
            db.execute("INSERT INTO portfolio_verification VALUES (?,?,?,'staff_review',NULL)", (eid, user["id"], now))
        label = "Достижение подтверждено" if state == "accepted" else "Достижение не подтверждено"
        svc.notify(db, row["university_id"], row["student_id"], "portfolio", eid,
                   f"achievement:{eid}:{key}", label, note, now)
        return eid
    return svc.command(db, user, "achievement.review", key, [eid, state, note, specific_request], apply)


def activity_award(db, user, slot, booking, now):
    eid = identifier()
    title = f'Участие · {slot["title"]}'[:140]
    details = "Посещение демонстрационного мероприятия подтверждено его организатором."
    db.execute("""
        INSERT INTO portfolio_entry (id,university_id,student_id,kind,title,content,created_at)
        VALUES (?,?,?,'achievement',?,?,?)
    """, (eid, slot["university_id"], booking["student_id"], title, details, now))
    db.execute("INSERT INTO portfolio_verification VALUES (?,?,?,'activity',?)", (eid, user["id"], now, booking["id"]))
    svc.notify(db, slot["university_id"], booking["student_id"], "portfolio", eid,
               f'activity-achievement:{booking["id"]}', "Добавлено подтверждённое достижение", title, now)


def exports(db, user, rid=None):
    query = "SELECT id,created_at,title,entry_ids_json FROM resume_document WHERE university_id=? AND student_id=?"
    if rid:
        return require(db.execute(query + " AND id=?", (*scope(user), rid)).fetchone())
    return list(db.execute(query + " ORDER BY created_at DESC,rowid DESC", scope(user)))


def resume_pdf(db, user, rid):
    return require(db.execute("SELECT pdf FROM resume_document WHERE university_id=? AND student_id=? AND id=?",
                              (*scope(user), rid)).fetchone())[0]


def create_resume(db, user, raw_title, selections, include_name, key):
    title = field(raw_title, "Заголовок резюме", 120)
    selections = sorted(set(selections))
    if not 1 <= len(selections) <= 12:
        raise DomainError(422, "Выбери от одной до 12 записей портфолио.")

    def apply():
        selected = [portfolio.entry(db, user, eid) for eid in selections]
        lines = [f"Имя: {user['full_name']}"] if include_name else []
        for row in selected:
            kind = portfolio.KINDS[row["kind"]]
            confirmation = " · подтверждено" if row["verified_at"] else ""
            lines.append(f"{kind}: {row['title']}{confirmation}")
            lines.extend(row["content"].splitlines())
        rid = identifier()
        db.execute("INSERT INTO resume_document VALUES (?,?,?,?,?,?,?)",
                   (rid, *scope(user), timestamp(), title, json.dumps(selections), render_document(title, lines)))
        return rid
    return svc.command(db, user, "resume.create", key, [title, selections, include_name], apply)
