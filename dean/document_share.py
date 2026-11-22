"""Time-limited and revocable access to an explicitly selected demo PDF."""
from datetime import timedelta
from uuid import UUID

from . import applications, auth
from .clock import now
from .db import write
from .domain import DomainError, identifier, require, role, timestamp

DURATIONS={"1":1,"24":24,"72":72}
DEMO_UNIVERSITY="spbstu-demo"


def own_document(db,user,did):
    role(user,"student")
    if user["university_id"]!=DEMO_UNIVERSITY:
        raise DomainError(403,"Поделиться можно выданным демонстрационным документом.")
    return applications.documents(db,user,did)


def links(db,user,did):
    own_document(db,user,did)
    return db.execute("""
        SELECT id,expires_at,revoked_at FROM document_share
        WHERE owner_id=? AND document_id=? ORDER BY created_at DESC,id LIMIT 20
    """,(user["id"],did)).fetchall()


def create(db,user,did,duration,confirmed,key):
    if not confirmed or duration not in DURATIONS:
        raise DomainError(422,"Выбери срок и подтверди передачу конкретного PDF.")
    try:
        key=UUID(key).hex
    except (TypeError,ValueError):
        raise DomainError(422,"Открой форму выдачи ссылки заново.") from None
    secret=auth.token()
    current=now()
    with write(db):
        own_document(db,user,did)
        if db.execute("SELECT 1 FROM document_share WHERE owner_id=? AND request_id=?",(user["id"],key)).fetchone():
            raise DomainError(409,"Ссылка уже была выдана.")
        count=db.execute("""
            SELECT count(*) FROM document_share WHERE owner_id=? AND document_id=?
               AND revoked_at IS NULL AND expires_at>?
        """,(user["id"],did,timestamp(current))).fetchone()[0]
        if count>=5:
            raise DomainError(409,"Отзови прежнюю ссылку перед созданием новой.")
        db.execute("INSERT INTO document_share VALUES (?,?,?,?,?,?,?,NULL)",
                   (identifier(),user["id"],did,auth.digest(secret),key,timestamp(current),
                    timestamp(current+timedelta(hours=DURATIONS[duration]))))
    return secret


def revoke(db,user,did,sid):
    with write(db):
        own_document(db,user,did)
        result=db.execute("""
            UPDATE document_share SET revoked_at=coalesce(revoked_at,?) WHERE id=? AND owner_id=? AND document_id=?
        """,(timestamp(),sid,user["id"],did))
        if not result.rowcount:
            raise DomainError(404,"Ссылка недоступна.")


def shared(db,secret):
    return require(db.execute("""
        SELECT d.id,d.pdf FROM document_share s JOIN document d ON d.id=s.document_id
        JOIN application a ON a.id=d.application_id AND a.university_id=d.university_id
        WHERE s.token_hash=? AND s.revoked_at IS NULL AND s.expires_at>? AND a.student_id=s.owner_id
          AND d.university_id=? AND a.status IN ('ready','received','completed')
    """,(auth.digest(secret),timestamp(),DEMO_UNIVERSITY)).fetchone())
