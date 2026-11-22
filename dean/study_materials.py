import hashlib

from . import attachment, personal_study, portfolio
from .domain import DomainError, field, identifier, require, role, timestamp
from .services import command


def materials(db, user, sid):
    personal_study.subject(db, user, sid)
    return list(db.execute("""
        SELECT id,title,kind,value,media_type,length(content) AS size,created_at
        FROM study_material WHERE subject_id=? ORDER BY created_at DESC,id
    """, (sid,)))


def value(db, user, mid, *, file_only=False):
    role(user, "student")
    sql = """
        SELECT m.* FROM study_material m JOIN personal_subject s ON s.id=m.subject_id
        WHERE m.id=? AND s.owner_id=?
    """
    return require(db.execute(sql + (" AND m.kind='file'" if file_only else ""), (mid, user["id"])).fetchone())


def add(db, user, sid, inputs, upload, key):
    media = data = None
    content = ""
    if upload:
        title, media, data = attachment.validated(*upload)
        kind = "file"
    else:
        kind = inputs.get("kind")
        title = field(inputs.get("title", ""), "Название", 120)
        if kind == "url":
            content = portfolio.valid_url(inputs.get("value", ""))
            if not content:
                raise DomainError(422, "Добавь ссылку.")
        elif kind == "note":
            content = field(inputs.get("value", ""), "Текст заметки", 1800)
        else:
            raise DomainError(422, "Выбери заметку, ссылку или вложение.")

    def apply():
        personal_study.subject(db, user, sid)
        counts = db.execute("""
            SELECT count(*),sum(kind='file') FROM study_material WHERE subject_id=?
        """, (sid,)).fetchone()
        if counts[0] >= 30 or kind == "file" and (counts[1] or 0) >= attachment.MAX_ACTIVE:
            raise DomainError(409, "Достигнут предел материалов предмета. Убери ненужный материал.")
        mid = identifier()
        db.execute("INSERT INTO study_material VALUES (?,?,?,?,?,?,?,?)",
                   (mid, sid, title, kind, content, media, data, timestamp()))
        return mid

    digest = hashlib.sha256(data).hexdigest() if data is not None else None
    return command(db, user, "study.material.add", key, [sid, title, kind, content, digest], apply)


def remove(db, user, sid, mid, key):
    def apply():
        if value(db, user, mid)["subject_id"] != sid:
            raise DomainError(404, "Материал недоступен.")
        db.execute("DELETE FROM study_material WHERE id=?", (mid,))
        return sid
    return command(db, user, "study.material.remove", key, [sid, mid], apply)


def search_rows(db, user):
    return db.execute("""
        SELECT m.id,m.subject_id,m.title,m.kind,m.value,s.title AS subject,
               CASE WHEN m.media_type='text/plain' THEN substr(m.content,1,16384) END AS text_excerpt
        FROM study_material m JOIN personal_subject s ON s.id=m.subject_id WHERE s.owner_id=?
    """, (user["id"],))
