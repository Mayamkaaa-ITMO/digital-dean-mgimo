"""Durable polling cursor and idempotent outbox for the configured test bot."""
from datetime import timedelta
import json
from zoneinfo import ZoneInfo

from . import auth, public_schedule, telegram
from .clock import now
from .db import write
from .domain import identifier, timestamp
from .telegram_transport import TransportError

POLL_LEASE=timedelta(seconds=50)
SEND_LEASE=timedelta(seconds=45)
MAX_ATTEMPTS=5


def poll_lease(db,config):
    current=now()
    stamp=timestamp(current)
    with write(db):
        db.execute("INSERT OR IGNORE INTO telegram_cursor(bot) VALUES (?)",(config.name,))
        row=db.execute("SELECT * FROM telegram_cursor WHERE bot=?",(config.name,)).fetchone()
        if row["lease_until"] and row["lease_until"]>stamp:
            return None
        key=identifier()
        db.execute("UPDATE telegram_cursor SET lease_id=?,lease_until=? WHERE bot=?",
                   (key,timestamp(current+POLL_LEASE),config.name))
        offset=row["next_offset"]
        if row["last_update_at"] and row["last_update_at"]<timestamp(current-timedelta(days=7)):
            offset=0
        return key,offset


def finish_poll(db,config,key):
    with write(db):
        db.execute("UPDATE telegram_cursor SET lease_id=NULL,lease_until=NULL WHERE bot=? AND lease_id=?",(config.name,key))


def process(db,config,key,offset,updates):
    selected=sorted((item for item in updates if isinstance(item,dict) and type(item.get("update_id")) is int
                     and 0<=item["update_id"]<2**53),key=lambda item:item["update_id"])
    processed=0
    with write(db):
        token=db.execute("SELECT lease_id FROM telegram_cursor WHERE bot=?",(config.name,)).fetchone()[0]
        if token!=key:
            return 0
        current=now()
        stamp=timestamp(current)
        db.execute("DELETE FROM telegram_update WHERE bot=? AND handled_at<?",
                   (config.name,timestamp(current-timedelta(days=8))))
        for item in selected:
            uid=item["update_id"]
            if uid<offset:
                continue
            request=telegram.private_message(item,config)
            chat_key=auth.digest(f"{config.name}:{request[0]}") if request else None
            added=db.execute("INSERT OR IGNORE INTO telegram_update VALUES (?,?,?,?)",
                             (config.name,uid,stamp,chat_key)).rowcount
            if added:
                telegram.handle_update(db,config,uid,item)
                processed+=1
            offset=uid+1
        if selected:
            db.execute("UPDATE telegram_cursor SET next_offset=?,last_update_at=? WHERE bot=?",(offset,stamp,config.name))
    return processed


def category(row):
    if row["kind"] in {"schedule","public_change"}:
        return "schedule"
    if row["kind"] in {"task","work_result"}:
        return row["kind"]
    if row["kind"]=="application" and any(tag in row["event_key"] for tag in (":needs_changes:",":ready:")):
        return "application"
    return None


def current_notice(db,user,notice):
    if not notice or not notice["active"] or notice["read_at"]:
        return False
    if notice["student_id"]!=user["id"] or notice["university_id"]!=user["university_id"]:
        return False
    target=notice["target_id"]
    if notice["kind"]=="task":
        task=db.execute("SELECT state,due_at FROM task WHERE id=? AND owner_id=?",(target,user["id"])).fetchone()
        return bool(task and task["state"]=="todo" and task["due_at"] and task["due_at"]>timestamp()
                    and notice["event_key"]==f'task:{target}:{task["due_at"]}')
    if notice["kind"]=="application":
        application=db.execute("SELECT status FROM application WHERE id=? AND student_id=?",(target,user["id"])).fetchone()
        return bool(application and application["status"] in {"needs_changes","ready"} and
                    f':{application["status"]}:' in notice["event_key"])
    if notice["kind"]=="schedule":
        return db.execute("""
            SELECT 1 FROM schedule_event WHERE id=? AND university_id=? AND group_id=? AND period_id=?
        """,(target,user["university_id"],user["group_id"],user["period_id"])).fetchone() is not None
    if notice["kind"]=="public_change":
        selection=public_schedule.context(db,user)
        return bool(selection and db.execute("""
            SELECT 1 FROM public_lesson WHERE id=? AND source_id=? AND group_id=?
        """,(target,*selection)).fetchone())
    if notice["kind"]=="work_result":
        return db.execute("SELECT 1 FROM coursework_student WHERE id=? AND student_id=? AND status='graded'",
                          (target,user["id"])).fetchone() is not None
    return category(notice) is not None


def notice_message(notice,config):
    kind=category(notice)
    text={"schedule":"В расписании появилось изменение.","task":"Приближается срок задачи.",
          "application":"Обновился статус заявления.","work_result":"Опубликован результат учебной работы."}[kind]
    if kind=="application":
        text="Заявление возвращено на исправление." if ":needs_changes:" in notice["event_key"] else "Документ готов."
    path={"schedule":"/schedule/","public_change":"/schedule/public/","task":"/tasks/",
          "application":"/requests/","work_result":"/work/"}[notice["kind"]]
    return text+telegram.tagline(config,path+notice["target_id"])


def cleanup(db,config):
    stamp=timestamp()
    db.execute("""
        UPDATE telegram_outbox SET state='uncertain',error='expired_lease',body='',chat_id=0,
            lease_id=NULL,lease_until=NULL WHERE bot=? AND state='sending' AND lease_until<=?
    """,(config.name,stamp))
    db.execute("""
        UPDATE telegram_outbox SET state='cancelled',body='',chat_id=0,lease_id=NULL,lease_until=NULL
        WHERE bot=? AND pair_id IN (SELECT id FROM telegram_pair WHERE expires_at<=?) AND state='pending'
    """,(config.name,stamp))
    db.execute("DELETE FROM telegram_pair WHERE expires_at<=?",(stamp,))


def collect(db,config):
    with write(db):
        cleanup(db,config)
        bindings=db.execute("""
            SELECT * FROM telegram_binding WHERE bot=? AND revoked_at IS NULL AND enabled=1
        """,(config.name,)).fetchall()
        for binding in bindings:
            user=telegram.linked_user(db,binding)
            if not user or user["role"]!="student" or not config.permits(binding["chat_id"]):
                continue
            chosen=set(json.loads(binding["categories_json"]))
            candidates=db.execute("""
                SELECT n.* FROM notification n WHERE n.student_id=? AND n.active=1 AND n.read_at IS NULL
                AND n.created_at>=? AND n.kind IN ('schedule','public_change','task','application','work_result')
                AND NOT EXISTS (SELECT 1 FROM telegram_outbox o WHERE o.notification_id=n.id AND o.binding_id=?)
                ORDER BY n.created_at DESC,n.id DESC LIMIT 100
            """,(user["id"],binding["linked_at"],binding["id"])).fetchall()
            for notice in candidates:
                if category(notice) in chosen and current_notice(db,user,notice):
                    telegram.enqueue(db,config.name,binding["chat_id"],category(notice),f'notice:{binding["id"]}:{notice["id"]}',
                                     notice_message(notice,config),binding=binding["id"],notification=notice["id"])
            local=now().astimezone(ZoneInfo(binding["timezone"]))
            if binding["summary"] and local.hour>=9:
                day=local.date().isoformat()
                summary=telegram.schedule_text(db,user)+telegram.tagline(config,"/schedule")
                telegram.enqueue(db,config.name,binding["chat_id"],"summary",
                                 f'summary:{binding["id"]}:{day}',"Сводка дня\n"+summary,binding=binding["id"])


def quiet(binding):
    moment=now().astimezone(ZoneInfo(binding["timezone"])).strftime("%H:%M")
    start,end=binding["quiet_start"],binding["quiet_end"]
    return start<=moment<end if start<=end else moment>=start or moment<end


def delivery_state(db,config,row):
    chat=row["chat_id"]
    if not config.permits(chat):
        return "cancelled"
    if row["pair_id"]:
        ok=db.execute("SELECT 1 FROM telegram_pair WHERE id=? AND bot=? AND chat_id=? AND expires_at>?",
                      (row["pair_id"],config.name,chat,timestamp())).fetchone()
        return "ready" if ok else "cancelled"
    if not row["binding_id"]:
        return "ready" if row["kind"]=="generic" else "cancelled"
    binding=db.execute("""
        SELECT * FROM telegram_binding WHERE id=? AND bot=? AND chat_id=? AND revoked_at IS NULL
    """,(row["binding_id"],config.name,chat)).fetchone()
    if not binding:
        return "cancelled"
    user=telegram.linked_user(db,binding)
    if not user:
        return "cancelled"
    if row["kind"]=="command":
        return "ready"
    if not binding["enabled"]:
        return "cancelled"
    if row["kind"]=="summary":
        if not binding["summary"]:
            return "cancelled"
        day=now().astimezone(ZoneInfo(binding["timezone"])).date().isoformat()
        if not row["key"].endswith(":"+day):
            return "cancelled"
    else:
        note=db.execute("SELECT * FROM notification WHERE id=?",(row["notification_id"],)).fetchone()
        if row["kind"] not in json.loads(binding["categories_json"]) or not current_notice(db,user,note):
            return "cancelled"
    return "defer" if quiet(binding) else "ready"


def claim(db,config):
    with write(db):
        stamp=timestamp()
        if db.execute("SELECT 1 FROM telegram_cursor WHERE bot=? AND send_after>?",(config.name,stamp)).fetchone():
            return None,None
        row=db.execute("""
            SELECT * FROM telegram_outbox WHERE bot=? AND state='pending' AND due_at<=?
            ORDER BY due_at,created_at,rowid LIMIT 1
        """,(config.name,stamp)).fetchone()
        if not row:
            return None,None
        status=delivery_state(db,config,row)
        if status=="defer":
            db.execute("UPDATE telegram_outbox SET due_at=? WHERE id=?",(timestamp(now()+timedelta(minutes=5)),row["id"]))
        elif status=="cancelled":
            db.execute("UPDATE telegram_outbox SET state='cancelled',body='',chat_id=0 WHERE id=?",(row["id"],))
        else:
            lease=identifier()
            db.execute("""
                UPDATE telegram_outbox SET state='sending',attempts=attempts+1,lease_id=?,lease_until=? WHERE id=?
            """,(lease,timestamp(now()+SEND_LEASE),row["id"]))
            return row,lease
        return row,None


def send_batch(db,config,transport,limit=20):
    counts={"sent":0,"failed":0,"uncertain":0,"retry":0}
    for _ in range(limit):
        row,lease=claim(db,config)
        if not row:
            break
        if not lease:
            continue
        error,receipt=None,None
        with write(db):
            live=db.execute("SELECT * FROM telegram_outbox WHERE id=? AND lease_id=?",(row["id"],lease)).fetchone()
            if not live:
                continue
            status=delivery_state(db,config,live)
            if status!="ready":
                if status=="defer":
                    db.execute("""
                        UPDATE telegram_outbox SET state='pending',due_at=?,lease_id=NULL,lease_until=NULL,
                            attempts=attempts-1 WHERE id=?
                    """,(timestamp(now()+timedelta(minutes=5)),row["id"]))
                else:
                    db.execute("""
                        UPDATE telegram_outbox SET state='cancelled',body='',chat_id=0,lease_id=NULL,lease_until=NULL WHERE id=?
                    """,(row["id"],))
                continue
        try:
            menu=telegram.keyboard(row["menu"])
            receipt=transport.send(row["chat_id"],row["body"],reply_markup=menu)
        except TransportError as failure:
            error=failure
        with write(db):
            active=db.execute("SELECT * FROM telegram_outbox WHERE id=? AND lease_id=?",(row["id"],lease)).fetchone()
            if not active:
                continue
            limited = error and error.code == "rate_limit"
            if limited:
                retry_at = timestamp(now()+timedelta(seconds=error.retry_after or 60))
                db.execute("UPDATE telegram_cursor SET send_after=? WHERE bot=?", (retry_at,config.name))
                if active["attempts"] < MAX_ATTEMPTS:
                    db.execute("""
                        UPDATE telegram_outbox SET state='pending',due_at=?,lease_id=NULL,lease_until=NULL,error=? WHERE id=?
                    """, (retry_at,error.code,row["id"]))
                    counts["retry"]+=1
                    break
            state="sent" if error is None else "uncertain" if error.code in {"uncertain","unavailable"} else "failed"
            db.execute("""
                UPDATE telegram_outbox SET state=?,body='',chat_id=0,lease_id=NULL,lease_until=NULL,error=?,
                       provider_message=? WHERE id=?
            """,(state,error.code if error else None,receipt,row["id"]))
            counts[state]+=1
            if error and error.code=="blocked" and row["binding_id"]:
                bound=db.execute("SELECT * FROM telegram_binding WHERE id=? AND revoked_at IS NULL",
                                 (row["binding_id"],)).fetchone()
                if bound:
                    telegram.revoke_in_transaction(db,bound)
            if limited:
                break
    return counts


def tick(db,config,transport):
    activated=poll_lease(db,config)
    if not activated:
        return {"busy":True}
    key,offset=activated
    failure=None
    processed=0
    try:
        updates=transport.updates(offset)
        processed=process(db,config,key,offset,updates)
    except TransportError as exc:
        failure=exc
    finally:
        finish_poll(db,config,key)
    collect(db,config)
    deliveries=send_batch(db,config,transport) if not failure else {}
    return {"processed":processed,"delivery":deliveries,
            "poll_error":failure.code if failure else None,"retry_after":failure.retry_after if failure else None}
