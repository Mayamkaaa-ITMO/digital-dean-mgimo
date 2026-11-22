"""Export limited calendar fields. Persist only the digest of a subscription secret."""
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
import json
from uuid import UUID

from . import auth, calendar_tools, public_schedule, services, study_context, workspace
from .db import write
from .domain import DomainError, identifier, require, role, timestamp
from .schedule import SQLiteScheduleProvider, local_midnight, local_value

EXPORT_SCOPES = {"study":"Учебное расписание", "personal":"Мои личные события", "all":"Учёба и мои личные события"}


@dataclass(frozen=True)
class Item:
    uid: str
    title: str
    start: str
    end: str | None
    zone: str
    all_day: bool
    version: int
    cancelled: bool = False
    place: str = ""


def period(first, last, scope):
    first, last = study_context.date_value(first), study_context.date_value(last)
    if not 0 <= (last-first).days < 93 or scope not in EXPORT_SCOPES:
        raise DomainError(422,"Выбери область и период до 93 дней.")
    return first,last


def personal_zone(db, user, row):
    source = db.execute("""
        SELECT uid,values_json,event_version,revision FROM imported_event
        WHERE event_id=? AND owner_id=?
    """,(row["id"],user["id"])).fetchone()
    if source:
        zone = json.loads(source["values_json"])[-1] if source["event_version"]==row["version"] else user["timezone"]
        return "import-"+source["uid"],zone,max(row["version"],source["revision"])
    return "event-"+row["id"],user["timezone"],row["version"]


def record_cancelled(db, user, row):
    stable_id, zone, version = personal_zone(db,user,row)
    db.execute("""
        INSERT INTO personal_calendar_cancellation VALUES (?,?,?,?,?,?,?,?,?,?)
    """, (row["id"],user["id"],stable_id,row["starts_at"],row["ends_at"],row["all_day"],
          row["repeat_until"],version+1,zone,timestamp()))


def export_items(db, user, selection, first, last, which):
    campus = calendar_tools.period_for(user,selection)
    stop = last+timedelta(days=1)
    since, until = local_midnight(first,campus.timezone),local_midnight(stop,campus.timezone)
    if which in {"study","all"}:
        if selection:
            for row in db.execute("""
                SELECT * FROM public_lesson WHERE source_id=? AND group_id=? AND starts_at<? AND ends_at>?
            """,(*selection,until,since)):
                yield Item("lesson-"+row["id"],row["subject"],row["starts_at"],row["ends_at"],campus.timezone,
                           False,row["version"],bool(row["cancelled"]))
        else:
            for row in SQLiteScheduleProvider(db).occurrences(campus,first,stop):
                yield Item("lesson-"+row.id,row.subject,row.starts_at,row.ends_at,campus.timezone,False,row.version)
            for row in services.results(db,user):
                if row["exam_at"] and since<=row["exam_at"]<until:
                    yield Item("exam-"+row["id"],row["subject"]+" · Экзамен",row["exam_at"],None,campus.timezone,False,1)
    if which == "study":
        return
    for row in db.execute("""
        SELECT * FROM personal_event WHERE owner_id=? AND university_id=? AND starts_at<?
        AND (ends_at>? OR repeat_until IS NOT NULL)
    """,(user["id"],user["university_id"],until,since)):
        uid,zone,version=personal_zone(db,user,row)
        skips={item[0] for item in db.execute("SELECT occurrence_day FROM personal_event_skip WHERE event_id=?",(row["id"],))}
        for day,start,end in workspace.occurrences(row,zone):
            if start<until and end>since:
                key = uid+"-"+day if row["repeat_until"] else uid
                yield Item(key,row["title"],start,end,zone,bool(row["all_day"]),version,day in skips,row["location"])
    for row in db.execute("""
        SELECT * FROM personal_calendar_cancellation WHERE owner_id=? AND starts_at<?
        AND (ends_at>? OR repeat_until IS NOT NULL)
    """,(user["id"],until,since)):
        for day,start,end in workspace.occurrences(row,row["timezone"]):
            if start<until and end>since:
                uid = row["stable_id"]+"-"+day if row["repeat_until"] else row["stable_id"]
                yield Item(uid,"Отменённое личное событие",start,end,row["timezone"],bool(row["all_day"]),row["version"],True)
    for row in db.execute("SELECT * FROM imported_event WHERE owner_id=? AND cancelled=1",(user["id"],)):
        _,_,start,end,all_day,zone=json.loads(row["values_json"])
        if start<until and end>since:
            yield Item("import-"+row["uid"],"Отменённое импортированное событие",start,end,zone,bool(all_day),row["revision"],True)


def escape(value):
    value = value.replace("\r\n","\n").replace("\r","\n")
    value = "".join(ch for ch in value if ch=="\n" or ord(ch)>=32 and ord(ch)!=127)
    return value.replace("\\","\\\\").replace("\n","\\n").replace(",","\\,").replace(";","\\;")


def fold(line):
    parts, text, count = [], "", 0
    for character in line:
        length=len(character.encode("utf-8"))
        if count+length>75:
            parts.append(text)
            text,count=" ",1
        text+=character
        count+=length
    return "\r\n".join([*parts,text])


def ics_time(value):
    return datetime.fromisoformat(value).astimezone(UTC).strftime("%Y%m%dT%H%M%SZ")


def event_lines(item, stamp, *, include_place=False):
    version = ["STATUS:CANCELLED"] if item.cancelled else ["STATUS:CONFIRMED"]
    lines=["BEGIN:VEVENT","UID:"+item.uid+"@digital-dean.local","DTSTAMP:"+stamp,
           f"SEQUENCE:{item.version}","SUMMARY:"+escape(item.title),*version]
    if include_place and item.place:
        lines.append("LOCATION:"+escape(item.place))
    for name,value in (("DTSTART",item.start),("DTEND",item.end)):
        if value:
            if item.all_day:
                lines.append(f"{name};VALUE=DATE:{local_value(value,item.zone):%Y%m%d}")
            else:
                lines.append(name+":"+ics_time(value))
    return [*lines,"END:VEVENT"]


def export(db,user,selection,first,last,which):
    role(user,"student")
    events = {}
    for item in export_items(db,user,selection,first,last,which):
        previous = events.get(item.uid)
        if previous is None or item.version >= previous.version:
            events[item.uid] = item
    return serialize(sorted(events.values(), key=lambda item: (item.start,item.uid)))


def serialize(items, *, include_place=False):
    lines=["BEGIN:VCALENDAR","VERSION:2.0","PRODID:-//Digital Dean//Personal Calendar//RU","CALSCALE:GREGORIAN"]
    stamp=ics_time(timestamp())
    for item in items:
        lines.extend(event_lines(item,stamp,include_place=include_place))
    lines.append("END:VCALENDAR")
    return ("\r\n".join(fold(line) for line in lines)+"\r\n").encode("utf-8")


def feeds(db,user):
    role(user,"student")
    return db.execute("""
        SELECT id,source_id,group_id,first_day,last_day,visibility,revoked_at FROM calendar_feed
        WHERE owner_id=? ORDER BY revoked_at IS NULL DESC,created_at DESC,id LIMIT 30
    """,(user["id"],)).fetchall()


def new_feed(db,user,selection,first,last,which,revision,request_id):
    role(user,"student")
    try:
        request_id=UUID(request_id).hex
    except (TypeError,ValueError):
        raise DomainError(422,"Открой форму подписки заново.") from None
    token=auth.token()
    with write(db):
        calendar_tools.verify_context(db,user,selection,revision)
        if db.execute("SELECT 1 FROM calendar_feed WHERE owner_id=? AND request_id=?",
                      (user["id"],request_id)).fetchone():
            raise DomainError(409,"Ссылка уже была показана. Управляй ей в списке подписок.")
        count=db.execute("SELECT count(*) FROM calendar_feed WHERE owner_id=? AND revoked_at IS NULL",(user["id"],)).fetchone()[0]
        if count>=10:
            raise DomainError(409,"Отзови ненужную подписку перед созданием новой.")
        db.execute("""
            INSERT INTO calendar_feed
            (id,owner_id,token_hash,request_id,university_id,assigned_group,assigned_period,
             source_id,group_id,first_day,last_day,visibility,created_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,(identifier(),user["id"],auth.digest(token),request_id,user["university_id"],user["group_id"],user["period_id"],
             *(selection or study_context.DEMO),
             first.isoformat(),last.isoformat(),which,timestamp()))
    return token


def revoke(db,user,feed_id):
    role(user,"student")
    with write(db):
        updated=db.execute("""
            UPDATE calendar_feed SET revoked_at=coalesce(revoked_at,?) WHERE owner_id=? AND id=?
        """,(timestamp(),user["id"],feed_id))
        if not updated.rowcount:
            raise DomainError(404,"Подписка недоступна.")


def subscription(db,token):
    row=require(db.execute("""
        SELECT f.*,a.role,a.period_id,
               u.timezone FROM calendar_feed f JOIN account a ON a.id=f.owner_id
        JOIN university u ON u.id=a.university_id
        WHERE f.token_hash=? AND f.revoked_at IS NULL AND a.role='student'
        AND f.university_id=a.university_id AND f.assigned_group=a.group_id AND f.assigned_period=a.period_id
    """,(auth.digest(token),)).fetchone())
    user={name:row[name] for name in ("university_id","period_id","role","timezone")}
    user.update(id=row["owner_id"],group_id=row["assigned_group"])
    first,last=period(row["first_day"],row["last_day"],row["visibility"])
    selection=study_context.parse_selection(row["source_id"]+"|"+row["group_id"]) if row["source_id"] else None
    return export(db,user,selection,first,last,row["visibility"])
