from urllib.parse import urlsplit
from uuid import NAMESPACE_URL, uuid5

from . import services as svc, workspace
from .applications import version_check
from .domain import DomainError, field, identifier, require, timestamp

KINDS = {"project": "Проект", "achievement": "Достижение"}
STATES = {"saved": "Сохранено", "preparing": "Готовлю отклик", "sent_manual": "Отметил, что откликнулся сам",
          "answer": "Получен ответ", "closed": "Закрыто"}


def seed_opportunities(db, university):
    for key, title, sponsor, description in (
        ("intern-data", "Учебная практика по аналитике", "Демо · студенческая лаборатория",
         "Составь короткий разбор синтетического набора данных и приложи описание своего проекта."),
        ("energy-team", "Работа в проекте по энергетике", "Демо · проектная группа",
         "Подготовь учебный пример моделирования электрической системы."),
    ):
        oid = uuid5(NAMESPACE_URL, f"dean:{university}:opportunity:{key}").hex
        db.execute("INSERT INTO opportunity VALUES (?, ?, ?, ?, ?) ON CONFLICT(id) DO NOTHING",
                   (oid, university, title, sponsor, description))


def entries(db, user):
    return list(db.execute("""
        SELECT e.*,v.verified_at FROM portfolio_entry e LEFT JOIN portfolio_verification v ON v.entry_id=e.id
        WHERE e.university_id=? AND e.student_id=? ORDER BY e.rowid DESC
    """, workspace.scope(user)))


def entry(db, user, eid):
    return require(db.execute("""
        SELECT e.*,v.verified_at,v.method FROM portfolio_entry e
        LEFT JOIN portfolio_verification v ON v.entry_id=e.id
        WHERE e.university_id=? AND e.student_id=? AND e.id=?
    """, (*workspace.scope(user), eid)).fetchone())


def valid_url(value):
    value = field(value, "Ссылка", 320, 0)
    if not value:
        return ""
    try:
        parts = urlsplit(value)
        if (parts.scheme == "https" and parts.hostname and parts.username is None
                and not any(c.isspace() or c == "\\" for c in value)):
            return value
    except ValueError:
        pass
    raise DomainError(422, "Укажи ссылку, которая начинается с https://.")


def save(db, user, inputs, key, eid=None, *, deleting=False):
    owner = workspace.scope(user)
    if deleting:
        values = ()
    else:
        kind = inputs.get("kind", "project")
        if kind not in KINDS:
            raise DomainError(422, "Выбери тип записи портфолио.")
        values = (kind, field(inputs.get("title", ""), "Название", 140),
                  field(inputs.get("content", ""), "Описание", 1600, 0),
                  valid_url(inputs.get("url", "")))

    def apply():
        if eid is None:
            new_id = identifier()
            db.execute("""
                INSERT INTO portfolio_entry (id,university_id,student_id,kind,title,content,url,created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (new_id, *owner, *values, timestamp()))
            return new_id
        current = entry(db, user, eid)
        version_check(current, inputs.get("version"))
        if deleting:
            db.execute("DELETE FROM portfolio_entry WHERE id=?", (eid,))
        else:
            if current["verified_at"] or db.execute("""
                SELECT 1 FROM achievement_review WHERE entry_id=? AND state='pending'
            """, (eid,)).fetchone():
                raise DomainError(409, "Подтверждённую или отправленную на проверку запись нельзя изменить.")
            db.execute("""
                UPDATE portfolio_entry SET kind=?,title=?,content=?,url=?,version=version+1 WHERE id=?
            """, (*values, eid))
        return eid

    return svc.command(db, user, "portfolio.save", key, [eid, deleting, values], apply)


def opportunities(db, user, oid=None):
    university, owner = workspace.scope(user)
    query = """
        SELECT o.*, t.status, t.note, t.updated_at FROM opportunity o LEFT JOIN opportunity_tracker t
        ON t.university_id=o.university_id AND t.opportunity_id=o.id AND t.student_id=?
        WHERE o.university_id=?
    """
    if oid is not None:
        return require(db.execute(query+" AND o.id=?", (owner, university, oid)).fetchone())
    return list(db.execute(query+" ORDER BY o.title", (owner, university)))


def track(db, user, oid, status, raw_note, key):
    opportunities(db, user, oid)
    if status not in STATES:
        raise DomainError(422, "Выбери статус отклика.")
    note = field(raw_note, "Комментарий", 1000,0)

    def apply():
        db.execute("""
            INSERT INTO opportunity_tracker VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(university_id,student_id,opportunity_id)
            DO UPDATE SET status=excluded.status,note=excluded.note,updated_at=excluded.updated_at
        """, (*workspace.scope(user),oid,status,note,timestamp()))
        return oid

    return svc.command(db,user,"opportunity.track",key,[oid,status,note],apply)
