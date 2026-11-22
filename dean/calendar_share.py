"""Expiring snapshots of explicitly selected personal events."""
from datetime import timedelta
from hashlib import sha256
import json
from uuid import UUID

from . import auth, calendar_export, calendar_import, calendar_tools, photo_import
from .clock import now
from .db import write
from .domain import DomainError, identifier, require, role, timestamp
from .schedule import local_value

MAX_EVENTS = 100
TTL = timedelta(days=7)


def choices(db,user,selection,raw_week):
    role(user,"student")
    first = photo_import.monday(raw_week)
    zone = calendar_tools.period_for(user,selection).timezone
    items = calendar_export.export_items(db,user,selection,first,first+timedelta(days=6),"personal")
    output = {}
    for row in sorted((row for row in items if not row.cancelled),key=lambda row:(row.start,row.uid)):
        record = {"key":sha256(row.uid.encode()).hexdigest()[:32],"title":row.title,"place":row.place,
                  "start":row.start,"end":row.end,"day":row.all_day,"zone":row.zone}
        identity = json.dumps((record,row.version),ensure_ascii=False).encode()
        output[sha256(identity).hexdigest()[:32]] = record
    return first,zone,output


def publish(db,user,selection,week,keys,revision,command):
    role(user,"student")
    try:
        command = UUID(command).hex
    except (TypeError,ValueError,AttributeError):
        raise DomainError(422,"Открой форму заново.") from None
    keys = set(keys)
    if not 1 <= len(keys) <= MAX_EVENTS:
        raise DomainError(422,"Отметь от одного до ста событий.")
    token, stamp = auth.token(), timestamp()
    with write(db):
        calendar_tools.verify_context(db,user,selection,revision)
        if db.execute("SELECT 1 FROM calendar_share WHERE owner_id=? AND request_id=?",
                      (user["id"],command)).fetchone():
            raise DomainError(409,"Ссылка уже создана. Её можно отозвать в списке.")
        total = db.execute("""
            SELECT count(*) FROM calendar_share WHERE owner_id=? AND revoked_at IS NULL AND expires_at>?
        """,(user["id"],stamp)).fetchone()[0]
        if total >= 10:
            raise DomainError(409,"Отзови одну из активных ссылок.")
        first,zone,available = choices(db,user,selection,week)
        if not keys.issubset(available):
            raise DomainError(409,"События изменились. Проверь неделю и выбери их заново.")
        events = [event for key,event in available.items() if key in keys]
        db.execute("""
            INSERT INTO calendar_share(id,owner_id,token_hash,request_id,monday,timezone,events_json,created_at,expires_at)
            VALUES (?,?,?,?,?,?,?,?,?)
        """,(identifier(),user["id"],auth.digest(token),command,first.isoformat(),zone,
             json.dumps(events,ensure_ascii=False),stamp,timestamp(now()+TTL)))
    return token


def mine(db,user):
    role(user,"student")
    return list(db.execute("""
        SELECT id,monday,created_at,expires_at,revoked_at FROM calendar_share WHERE owner_id=?
        ORDER BY created_at DESC,rowid DESC LIMIT 30
    """,(user["id"],)))


def revoke(db,user,sid):
    role(user,"student")
    changed = db.execute("""
        UPDATE calendar_share SET revoked_at=coalesce(revoked_at,?) WHERE owner_id=? AND id=?
    """,(timestamp(),user["id"],sid))
    if not changed.rowcount:
        raise DomainError(404,"Ссылка недоступна.")


def shared(db,token):
    return require(db.execute("""
        SELECT * FROM calendar_share WHERE token_hash=? AND revoked_at IS NULL AND expires_at>?
    """,(auth.digest(token),timestamp())).fetchone())


def copy(db,user,selection,token,command):
    role(user,"student")
    row = shared(db,token)
    if row["owner_id"] == user["id"]:
        raise DomainError(422,"Эти события уже в твоём календаре.")
    events = []
    for item in json.loads(row["events_json"]):
        if item["day"]:
            start,end = (local_value(item[field],item["zone"]).date().isoformat() for field in ("start","end"))
        else:
            start,end = (local_value(item[field],row["timezone"]).replace(tzinfo=None).isoformat(timespec="seconds")
                         for field in ("start","end"))
        events.append({"event_key":item["key"],"source_event_id":item["key"],"subject":item["title"],
                       "kind":"","room":item["place"],"local_start":start,"local_end":end})
    source = "calendar-share-"+sha256(row["owner_id"].encode()).hexdigest()[:24]
    draft = {"schema_version":"1.0","source":{"id":source,"kind":"shared"},
             "scope":{"period_id":row["monday"],"timezone":row["timezone"]},"events":events}
    return calendar_import.start(db,user,selection,"draft",json.dumps(draft,ensure_ascii=False).encode(),command)
