"""Validate and confirm a structured personal calendar draft."""
import csv
from datetime import timedelta
import hashlib
from io import StringIO
import json
import re
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from . import calendar_tools, study_context, workspace
from .domain import DomainError, field, identifier, require, role, timestamp
from .schedule import edit_time, local_midnight
from .services import command

MAX_BYTES = 64 * 1024
MAX_ROWS = 100
CSV_TEXT_LIMIT = 6000
# An encoded UTF-16 code unit can take up to nine bytes, plus small form fields.
CSV_FORM_LIMIT = CSV_TEXT_LIMIT * 9 + 512
CSV_FIELDS = ("key","subject","kind","start","end","room","status")
CSV_HEADER = ",".join(CSV_FIELDS)
CSV_EXAMPLE = (CSV_HEADER + "\n"
               "demo-1,Учебный практикум,seminar,2026-11-20T16:00,2026-11-20T17:00,А-104,active\n")
EDITABLE = ("key","subject","kind","start","end","room","status")
CLOCK = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}(?::[0-5][0-9])?")


def raw_text(value, limit):
    if value is None:
        return ""
    if not isinstance(value, str) or len(value) > limit or any(ord(c) < 32 for c in value):
        raise DomainError(422, "Поля расписания должны содержать короткий текст без управляющих знаков.")
    return value


def timezone(value):
    name = raw_text(value, 80)
    if not name or not re.fullmatch(r"[A-Za-z0-9_+\-/]+", name):
        raise DomainError(422, "Укажи часовой пояс IANA, например Europe/Moscow.")
    try:
        return ZoneInfo(name).key
    except (ValueError, ZoneInfoNotFoundError):
        raise DomainError(422, "Такой часовой пояс не найден.") from None


def csv_rows(text):
    records = csv.reader(StringIO(text, newline=""), strict=True)
    line, rows = 1, []
    try:
        if tuple(next(records, ())) != CSV_FIELDS:
            raise DomainError(422, "Строка 1. Замени заголовок CSV на " + CSV_HEADER + ".")
        line = records.line_num + 1
        for row in records:
            if row:
                if len(row) != len(CSV_FIELDS):
                    raise DomainError(422, f"Строка {line}. Нужно {len(CSV_FIELDS)} колонок.")
                rows.append({"line": line, **dict(zip(CSV_FIELDS, row)), "warnings": []})
            line = records.line_num + 1
    except csv.Error as error:
        raise DomainError(422, f"Строка {line}. Проверь кавычки и разделение колонок.") from error
    return rows


def draft_rows(text, format_name, user, selection):
    if format_name == "csv":
        default_zone = calendar_tools.period_for(user, selection).timezone
        return [format_name, *(selection or study_context.DEMO)], default_zone, csv_rows(text)
    draft = json.loads(text)
    if not isinstance(draft, dict) or draft.get("schema_version") != "1.0":
        raise DomainError(422, "Поддерживается ScheduleDraft 1.0.")
    scope, source, events = (draft.get(name) for name in ("scope","source","events"))
    warnings = draft.get("warnings", [])
    if not isinstance(scope, dict) or not isinstance(source, dict) or not isinstance(events,list):
        raise DomainError(422, "Укажи scope, source и events по контракту.")
    if not isinstance(warnings,list) or len(warnings) > MAX_ROWS*4 or len(events) > MAX_ROWS:
        raise DomainError(422, "Сократи число строк или предупреждений.")
    if any(not isinstance(item,dict) for item in events + warnings):
        raise DomainError(422, "Строки ScheduleDraft должны быть объектами.")
    identity = ["draft", raw_text(source.get("id"),120),
                *(raw_text(scope.get(name),120) for name in ("university_id","group_id","period_id"))]
    if not identity[1]:
        raise DomainError(422, "Для повторного импорта укажи стабильный source.id.")
    rows = []
    for index,event in enumerate(events,1):
        key = raw_text(event.get("event_key"),120)
        notes = [{"field":raw_text(warning.get("field"),80),
                  "message":raw_text(warning.get("message"),240)}
                 for warning in warnings if warning.get("event_key") in (None,key)]
        rows.append({"line":index,"key":raw_text(event.get("source_event_id"),120) or key,
                     "subject":event.get("subject"),"kind":event.get("kind"),"start":event.get("local_start"),
                     "end":event.get("local_end"),"room":event.get("room"),"status":"active","warnings":notes})
    return identity, raw_text(scope.get("timezone"),80), rows


def import_row(db, user, row, zone, origin, *, edited=False):
    try:
        values = {name:raw_text(row.get(name),160 if name=="subject" else 120) for name in EDITABLE}
    except DomainError as error:
        raise DomainError(error.status, f'Строка {row["line"]}. {error.message}') from error
    values["status"] = values["status"] or "active"
    values["key"] = values["key"].strip()
    current = current_event(db, user, origin, values["key"])
    event = db.execute("SELECT version FROM personal_event WHERE id=? AND owner_id=?",
                       (current["event_id"],user["id"])).fetchone() if current and current["event_id"] else None
    return {**values,"line":row["line"],"warnings":row["warnings"],"reviewed":edited and row.get("reviewed",False),
            "basis":current["revision"] if current else 0,"event_basis":event["version"] if event else None,
            "replace":edited and row.get("replace",False)}


def current_event(db, user, origin, event_key):
    return db.execute("""
        SELECT * FROM imported_event WHERE owner_id=? AND origin_key=? AND external_id=?
    """, (user["id"], origin, event_key)).fetchone()


def parse_local(raw, zone):
    if not CLOCK.fullmatch(raw):
        raise DomainError(422, "Время должно быть ГГГГ-ММ-ДДTЧЧ:ММ или с секундами.")
    study_context.date_value(raw[:10])
    return timestamp(edit_time(raw[:16],zone) + timedelta(seconds=int(raw[17:]) if len(raw)==19 else 0))


def normalized(row, zone):
    key = field(row["key"], "Ключ", 120)
    if row["status"] not in {"active","cancelled"}:
        raise DomainError(422, "Статус строки — active или cancelled.")
    if row["status"] == "cancelled":
        return key, None
    subject = field(row["subject"], "Предмет", 120)
    room = field(row["room"], "Помещение", 120, 0)
    zone = timezone(zone)
    all_day = len(row["start"]) == len(row["end"]) == 10
    if all_day:
        first, last = study_context.date_value(row["start"]), study_context.date_value(row["end"])
        start, end = local_midnight(first,zone), local_midnight(last,zone)
    else:
        start, end = parse_local(row["start"],zone), parse_local(row["end"],zone)
    if not start < end or calendar_tools.slot_time(end)-calendar_tools.slot_time(start)>timedelta(days=30):
        raise DomainError(422, "Конец должен быть позже начала, длительность до 30 дней.")
    return key, [subject, room, start, end, int(all_day), zone]


def same_personal_event(db, user, values, exclude=None):
    subject, _, start, end, _, _ = values
    rows = db.execute("""
        SELECT id,title FROM personal_event WHERE university_id=? AND owner_id=? AND starts_at=? AND ends_at=?
        AND all_day=0 AND id IS NOT ?
    """, (*workspace.scope(user), start, end, exclude))
    title = " ".join(subject.casefold().split())
    return any(" ".join(row["title"].casefold().split()) == title for row in rows)


def errors(rows, zone, db, user, origin, photo_week=""):
    rows = [row for row in rows if not row.get("excluded")]
    counts = {}
    for row in rows:
        key = row["key"].strip()
        counts[key] = counts.get(key,0) + 1
    failures, seen = {}, set()
    if photo_week:
        last = (study_context.date_value(photo_week) + timedelta(days=7)).isoformat()
    for row in rows:
        try:
            key, dates = normalized(row,zone)
            if counts[key]>1:
                raise DomainError(422, "Ключ встречается в нескольких строках.")
            if dates is None and current_event(db,user,origin,key) is None:
                raise DomainError(422, "Отмена допустима для ранее импортированного ключа.")
            if row["warnings"] and not row["reviewed"]:
                raise DomainError(422, "Проверь предупреждения и отметь строку как сверенную.")
            if photo_week:
                if (dates is None or dates[4] or not photo_week <= row["start"][:10] < last
                        or row["end"][:10] != row["start"][:10]):
                    raise DomainError(422, "Пара должна начинаться и заканчиваться в один день выбранной недели.")
                identity = (" ".join(dates[0].casefold().split()), *dates[2:4])
                if identity in seen:
                    raise DomainError(422, "Такая пара уже есть в черновике. Исправь её или исключи повтор.")
                before = current_event(db, user, origin, key)
                if (before and before["event_id"] and before["values_json"] != json.dumps(dates, ensure_ascii=False)
                        and same_personal_event(db, user, dates, before["event_id"])):
                    raise DomainError(422, "После правки эта пара совпадёт с другой записью календаря. Уточни время или исключи строку.")
                seen.add(identity)
        except DomainError as error:
            failures[row["line"]] = error.message
    return failures


def get(db,user,did):
    role(user,"student")
    return require(db.execute("SELECT * FROM calendar_import WHERE id=? AND owner_id=?",(did,user["id"])).fetchone())


def repeated_photo(db, user, image_key):
    row = db.execute("""
        SELECT i.id FROM photo_import_context c JOIN calendar_import i ON i.id=c.import_id
        WHERE c.image_key=? AND i.owner_id=? AND i.state<>'cancelled'
        ORDER BY i.created_at DESC,i.rowid DESC LIMIT 1
    """, (image_key, user["id"])).fetchone()
    return row["id"] if row else None


def photo_week(db, user, did):
    row = db.execute("""
        SELECT c.week_of FROM photo_import_context c JOIN calendar_import i ON i.id=c.import_id
        WHERE i.owner_id=? AND i.id=?
    """, (user["id"], did)).fetchone()
    return row["week_of"] if row else ""


def impacts(rows, zone, db, user, origin, failures, *, from_photo=False):
    result, intervals = {}, {}
    for row in rows:
        line = row["line"]
        if row.get("excluded") or line in failures:
            continue
        key, values = normalized(row, zone)
        before = current_event(db, user, origin, key)
        if values is None:
            result[line] = "Событие будет отменено"
        elif before and not before["cancelled"] and before["values_json"] == json.dumps(values, ensure_ascii=False):
            if from_photo and not row["replace"] and row["event_basis"] != before["event_version"]:
                result[line] = "Сохранится ручная правка" if before["event_id"] else "Удалённая запись останется удалённой"
            else:
                result[line] = "Событие изменится" if from_photo and row["replace"] else "Событие уже есть"
        elif from_photo and (before is None or not before["event_id"]) and same_personal_event(db, user, values):
            result[line] = "Такая пара уже есть в календаре"
        else:
            result[line] = "Событие изменится" if before and not before["cancelled"] else "Добавится событие"
        if values and not values[4]:
            intervals[line] = values[2:4]
    overlap = {line for line, (start, end) in intervals.items()
               if any(other != line and start < stop and end > low
                      for other, (low, stop) in intervals.items())}
    return result, overlap


def start(db,user,selection,format_name,body,key, *, photo=None):
    role(user,"student")
    if not 0 < len(body) <= MAX_BYTES:
        raise DomainError(413,"Нужен файл CSV или JSON до 64 КиБ.")
    try:
        text = body.decode("utf-8-sig")
        identity, zone, rows = draft_rows(text,format_name,user,selection)
    except (UnicodeError, json.JSONDecodeError, csv.Error, RecursionError) as error:
        raise DomainError(422,"Не удалось разобрать UTF-8 CSV или ScheduleDraft JSON.") from error
    if not 1 <= len(rows) <= MAX_ROWS:
        raise DomainError(422,"Черновик должен содержать от 1 до 100 строк.")
    origin = hashlib.sha256(json.dumps(identity,ensure_ascii=False).encode()).hexdigest()

    def apply():
        if photo and (existing := repeated_photo(db, user, photo[1])):
            return existing
        did = identifier()
        editable = [import_row(db,user,row,zone,origin) for row in rows]
        db.execute("INSERT INTO calendar_import(id,owner_id,origin_key,zone,rows_json,created_at) VALUES (?,?,?,?,?,?)",
                   (did,user["id"],origin,zone,json.dumps(editable,ensure_ascii=False),timestamp()))
        if photo:
            db.execute("INSERT INTO photo_import_context VALUES (?,?,?)", (did, *photo))
        return did
    fingerprint = [format_name,origin,hashlib.sha256(body).hexdigest()]
    if photo:
        fingerprint.append(photo)
    return command(db,user,"calendar.import.start",key,fingerprint,apply)


def apply_row(db,user,draft,row, *, from_photo=False):
    key,data = normalized(row,draft["zone"])
    origin = draft["origin_key"]
    old = current_event(db,user,origin,key)
    cancelled = data is None
    normalized_json = old["values_json"] if cancelled else json.dumps(data,ensure_ascii=False)
    if (old and cancelled==bool(old["cancelled"]) and old["values_json"]==normalized_json
            and not (from_photo and row["replace"])):
        return
    if from_photo and (old is None or not old["event_id"]) and same_personal_event(db, user, data):
        return
    if (old["revision"] if old else 0)!=row["basis"]:
        raise DomainError(409,"Данные импорта изменились. Создай новый предпросмотр.")
    event = workspace.event(db,user,old["event_id"]) if old and old["event_id"] else None
    if (event["version"] if event else None)!=row["event_basis"]:
        raise DomainError(409,"Личное событие изменилось после предпросмотра. Перепроверь эту строку.")
    if old and not old["cancelled"] and (not event or event["version"] != old["event_version"]) and not row["replace"]:
        raise DomainError(409,"Есть ручная правка. Отметь её замену в конкретной строке.")
    stamp, event_id, version = timestamp(), None, None
    if cancelled:
        if event:
            db.execute("DELETE FROM personal_event WHERE id=?",(event["id"],))
    else:
        title,room,start,end,all_day,_ = data
        description = "Личный импорт календаря"
        if from_photo:
            description = "Импорт из фото" + (" · " + row["kind"] if row["kind"] else "")
        if event:
            version = event["version"]+1
            event_id = event["id"]
            db.execute("""
                UPDATE personal_event SET title=?,location=?,starts_at=?,ends_at=?,all_day=?,version=?,repeat_until=NULL,
                    description=CASE WHEN ? THEN ? ELSE description END
                WHERE id=?
            """, (title,room,start,end,all_day,version,from_photo,description,event_id))
            db.execute("DELETE FROM personal_event_skip WHERE event_id=?",(event_id,))
        else:
            version = 1
            event_id = workspace.add_event(db,user,(title,description,room,start,end,all_day,None))
    db.execute("""
        INSERT INTO imported_event(owner_id,origin_key,external_id,uid,event_id,event_version,
                                   values_json,cancelled,revision,changed_at) VALUES (?,?,?,?,?,?,?,?,1,?)
        ON CONFLICT(owner_id,origin_key,external_id) DO UPDATE SET event_id=excluded.event_id,
            event_version=excluded.event_version,values_json=excluded.values_json,cancelled=excluded.cancelled,
            revision=imported_event.revision+1,changed_at=excluded.changed_at
    """, (user["id"],origin,key,identifier(),event_id,version,normalized_json,int(cancelled),stamp))


def change(db,user,did,action,fields,key):
    def apply():
        draft = get(db,user,did)
        if action=="confirm" and draft["state"]=="confirmed":
            return did
        if str(draft["version"])!=fields.get("version") or draft["state"]!="draft":
            raise DomainError(409,"Черновик изменился. Открой его заново.")
        week = photo_week(db, user, did)
        rows,zone = json.loads(draft["rows_json"]), draft["zone"]
        state = "draft"
        if action=="cancel":
            state = "cancelled"
        elif action=="zone":
            if week:
                raise DomainError(422, "Для фото используется часовой пояс выбранного календаря.")
            zone=timezone(fields.get("zone"))
        elif action in {"row", "exclude"}:
            index=next((n for n,row in enumerate(rows) if str(row["line"])==fields.get("line")),None)
            if index is None:
                raise DomainError(422,"Выбери строку.")
            old=rows[index]
            if action == "exclude":
                if fields.get("excluded") not in {"0", "1"}:
                    raise DomainError(422,"Выбери действие для строки.")
                old["excluded"] = fields["excluded"] == "1"
            else:
                value={name:fields.get(name,"") for name in EDITABLE}
                if week:
                    value.update(key=old["key"], status="active")
                value.update(line=old["line"],warnings=old["warnings"],reviewed=fields.get("reviewed")=="1",
                             replace=fields.get("replace")=="1")
                rows[index]=import_row(db,user,value,zone,draft["origin_key"],edited=True)
                if old.get("excluded"):
                    rows[index]["excluded"] = True
        elif action=="confirm":
            selected = [row for row in rows if not row.get("excluded")]
            if not selected:
                raise DomainError(422, "Оставь хотя бы одну строку для импорта.")
            failed=errors(selected,zone,db,user,draft["origin_key"],week)
            if failed:
                line=min(failed)
                raise DomainError(422,f"Строка {line}. {failed[line]}")
            for row in selected:
                apply_row(db,user,draft,row,from_photo=bool(week))
            state="confirmed"
        db.execute("""
            UPDATE calendar_import SET state=?,zone=?,rows_json=?,version=version+1 WHERE id=?
        """, (state,zone,json.dumps(rows,ensure_ascii=False),did))
        return did
    return command(db,user,"calendar.import."+action,key,[did,fields],apply)
