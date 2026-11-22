"""Versioned transfers containing only the authenticated owner's personal workspace."""

import hashlib
import json
import re
from uuid import NAMESPACE_URL, uuid5

from . import calendar_tools, preparation, profile_tools, reminders, study_context, workspace
from .domain import DomainError, field, identifier, require, role, timestamp
from .schedule import local_value
from .services import command

MAX_BYTES = 2 * 1024 * 1024
MAX_ITEMS = 500
FORMATS = ("tasks", "notes", "events")
TEXT = {"tasks": ("title","description","subject_id","priority","state","due_at"),
        "notes": ("title","target_kind","target","body"),
        "events": ("title","description","location","starts_at","ends_at","repeat_until")}
DIGEST_ID = re.compile(r"[a-f0-9]{32}")
SETTINGS = {"reminders","hide_preview"}


def settings(db,user):
    return {"reminders":reminders.settings(db,user),"hide_preview":profile_tools.hidden_preview(db,user)}


def notice_id(owner,kind,key):
    return uuid5(NAMESPACE_URL, f"dean-private-export:{owner}:{kind}:{key}").hex


def detached(db,user):
    return db.execute("SELECT * FROM detached_personal_note WHERE owner_id=? ORDER BY created_at,id",(user["id"],))


def private_notes(db,user):
    for row in db.execute("SELECT * FROM note WHERE university_id=? AND owner_id=? ORDER BY id",workspace.scope(user)):
        kind, target=next((kind,row[column]) for kind,column in workspace.NOTE_TARGETS.items() if row[column] is not None)
        yield {"id":row["id"],"title":"Личная заметка","target_kind":kind,"target":target,"body":row["body"]}
    for row in db.execute("""
        SELECT n.lesson_id,n.body,p.subject FROM public_note n JOIN public_lesson p ON p.id=n.lesson_id
        WHERE n.owner_id=? AND n.body<>'' ORDER BY n.lesson_id
    """,(user["id"],)):
        yield {"id":notice_id(user["id"],"public",row["lesson_id"]),"title":row["subject"],
               "target_kind":"separate","target":"","body":row["body"]}
    for row in db.execute("""
        SELECT id,title,note FROM personal_subject WHERE owner_id=? AND note<>'' ORDER BY id
    """,(user["id"],)):
        yield {"id":notice_id(user["id"],"subject",row["id"]),"title":row["title"],
               "target_kind":"separate","target":"","body":row["note"]}
    for row in detached(db,user):
        yield {"id":row["id"],"title":row["title"],"target_kind":"separate","target":"","body":row["body"]}


def snapshot(db,user):
    role(user,"student")
    tasks = []
    for row in workspace.tasks(db,user):
        info = {name:row[name] or "" for name in TEXT["tasks"]}
        info["duration_minutes"]=preparation.effort(db,row["id"])
        info["subtasks"]=[{"title":part["title"],"done":bool(part["done"])}
                          for part in workspace.subtasks(db,user,row["id"])]
        tasks.append({"id":row["id"],**info})
    events = []
    for row in db.execute("SELECT * FROM personal_event WHERE university_id=? AND owner_id=? ORDER BY id",workspace.scope(user)):
        data={name:row[name] or "" for name in TEXT["events"]}
        skipped=[item[0] for item in db.execute("""
            SELECT occurrence_day FROM personal_event_skip WHERE event_id=? ORDER BY occurrence_day
        """,(row["id"],))]
        events.append({"id":row["id"],**data,"all_day":bool(row["all_day"]),"skipped":skipped})
    return {"format":"dean.personal", "version":1, "tasks":tasks,"notes":list(private_notes(db,user)),
            "events":events,"settings":settings(db,user)}


def exact(obj, keys):
    if not isinstance(obj,dict) or set(obj) != set(keys):
        raise DomainError(422,"Неожиданные или отсутствующие поля в файле.")


def string(value, label, limit, minimum=0):
    if not isinstance(value,str):
        raise DomainError(422,f"Поле «{label}» должно быть текстом.")
    return field(value,label,limit,minimum)


def valid_id(value):
    if not isinstance(value,str) or not DIGEST_ID.fullmatch(value):
        raise DomainError(422,"Ожидается идентификатор личной записи.")
    return value


def normalize_record(kind,row,user):
    extra={"tasks":("duration_minutes","subtasks"),"notes":(),"events":("all_day","skipped")}[kind]
    exact(row,("id",*TEXT[kind],*extra))
    out={name:string(row[name],name,1600) for name in TEXT[kind]}
    out.update(id=valid_id(row["id"]), title=string(row["title"],"Название",160 if kind=="notes" else 120,1))
    if kind=="tasks":
        if out["priority"] not in workspace.PRIORITIES or out["state"] not in {"todo","done"}:
            raise DomainError(422,"Некорректное состояние или приоритет задачи.")
        if out["due_at"]:
            calendar_tools.slot_time(out["due_at"])
        out["subject_id"]=string(row["subject_id"],"Предмет",120)
        estimate, parts=row["duration_minutes"],row["subtasks"]
        if estimate is not None and (type(estimate) is not int or not 1<=estimate<=1440):
            raise DomainError(422,"Некорректная длительность задачи.")
        if not isinstance(parts,list) or len(parts)>100:
            raise DomainError(422,"Не больше 100 подзадач на задачу.")
        out.update(duration_minutes=estimate,subtasks=[])
        for part in parts:
            exact(part,{"title","done"})
            if type(part["done"]) is not bool:
                raise DomainError(422,"Состояние подзадачи должно быть true или false.")
            out["subtasks"].append({"title":string(part["title"],"Подзадача",120,1),"done":part["done"]})
    elif kind=="notes":
        if out["target_kind"] not in {"task","lesson","subject","separate"}:
            raise DomainError(422,"Некорректный тип заметки.")
        out["target"]=string(row["target"],"Связь заметки",120)
        out["body"]=string(row["body"],"Заметка",1600,1)
    else:
        start,end=calendar_tools.slot_time(out["starts_at"]),calendar_tools.slot_time(out["ends_at"])
        if not start<end or type(row["all_day"]) is not bool or not isinstance(row["skipped"],list):
            raise DomainError(422,"Некорректный личный интервал или формат повторов.")
        out["location"]=string(row["location"],"Место",120)
        out["all_day"],out["skipped"]=row["all_day"],[]
        if out["repeat_until"]:
            first=local_value(out["starts_at"],user["timezone"]).date()
            last=study_context.date_value(out["repeat_until"])
            if not 0<=(last-first).days//7<workspace.MAX_OCCURRENCES:
                raise DomainError(422,"Серия превышает поддерживаемый предел повторов.")
        valid_days={day for day,_,_ in workspace.occurrences(out,user["timezone"])} if out["repeat_until"] else set()
        if len(row["skipped"])>workspace.MAX_OCCURRENCES or any(
                not isinstance(value,str) or value not in valid_days for value in row["skipped"]):
            raise DomainError(422,"Исключение вне серии личных событий.")
        out["skipped"]=sorted(set(row["skipped"]))
    return out


def validate(raw,user):
    if not 0<len(raw)<=MAX_BYTES:
        raise DomainError(413,"Файл переноса должен занимать до 2 МиБ.")
    try:
        data=json.loads(raw)
    except (UnicodeError,ValueError,RecursionError):
        raise DomainError(422,"Не удалось прочитать JSON.") from None
    exact(data,{"format","version","settings",*FORMATS})
    if data["format"]!="dean.personal" or type(data["version"]) is not int or data["version"]!=1:
        raise DomainError(422,"Версия формата переноса не поддерживается.")
    expected=data["settings"]
    exact(expected,SETTINGS)
    exact(expected["reminders"],reminders.OPTIONAL)
    if type(expected["hide_preview"]) is not bool or any(type(value) is not bool for value in expected["reminders"].values()):
        raise DomainError(422,"Настройки должны содержать true или false.")
    if any(not isinstance(data[kind],list) for kind in FORMATS) or sum(len(data[kind]) for kind in FORMATS)>MAX_ITEMS:
        raise DomainError(422,"Один перенос поддерживает до 500 записей.")
    for kind in FORMATS:
        rows=[]
        for index,row in enumerate(data[kind],1):
            try:
                rows.append(normalize_record(kind,row,user))
            except DomainError as error:
                raise DomainError(error.status,f"{kind}, строка {index}. {error.message}") from error
        if len({row["id"] for row in rows})!=len(rows):
            raise DomainError(422,"В файле повторяется идентификатор записи.")
        data[kind]=rows
    return data


def fingerprint(data):
    return hashlib.sha256(json.dumps(data,ensure_ascii=False,sort_keys=True).encode()).hexdigest()


def receipt(db,user,kind,external):
    return db.execute("""
        SELECT * FROM personal_restore_receipt WHERE owner_id=? AND kind=? AND external_id=?
    """,(user["id"],kind,external)).fetchone()


def compare(db,user,data,restore_settings):
    existing=snapshot(db,user)
    result={kind:{} for kind in FORMATS}
    for kind in FORMATS:
        checked={row["id"]:row for row in existing[kind]}
        for row in data[kind]:
            prev=receipt(db,user,kind,row["id"])
            if prev:
                state="same" if prev["fingerprint"]==fingerprint(row) else "conflict"
            elif row["id"] in checked:
                state="same" if checked[row["id"]]==row else "conflict"
            else:
                state="new"
            result[kind][row["id"]]=state
    result["settings_before"]=existing["settings"] if restore_settings else None
    return result


def previews(db,user):
    role(user,"student")
    return db.execute("SELECT id,state,created_at FROM personal_restore WHERE owner_id=? ORDER BY created_at DESC,id LIMIT 20",
                      (user["id"],)).fetchall()


def draft(db,user,pid):
    role(user,"student")
    return require(db.execute("SELECT * FROM personal_restore WHERE id=? AND owner_id=?",(pid,user["id"])).fetchone())


def stage(db,user,raw,restore_settings,key):
    role(user,"student")
    data=validate(raw,user)
    def apply():
        pid=identifier()
        review=compare(db,user,data,restore_settings)
        db.execute("""
            INSERT INTO personal_restore(id,owner_id,payload_json,review_json,restore_settings,created_at)
            VALUES (?,?,?,?,?,?)
        """,(pid,user["id"],json.dumps(data,ensure_ascii=False),json.dumps(review),int(restore_settings),timestamp()))
        return pid
    return command(db,user,"profile.restore.stage",key,[fingerprint(data),restore_settings],apply)


def install_task(db,user,row,subjects):
    subject=row["subject_id"] if row["subject_id"] in subjects else None
    tid=workspace.add_task(db,user,(row["title"],row["description"],subject,row["due_at"] or None,row["priority"]))
    db.execute("UPDATE task SET state=? WHERE id=?",(row["state"],tid))
    db.executemany("INSERT INTO subtask VALUES (?,?,?,?)",[(identifier(),tid,part["title"],int(part["done"])) for part in row["subtasks"]])
    if row["duration_minutes"]:
        db.execute("INSERT INTO task_effort VALUES (?,?)",(tid,row["duration_minutes"]))
    return tid


def install_note(db,user,row):
    kind,target=row["target_kind"],row["target"]
    if kind=="task" and (found:=receipt(db,user,"tasks",target)):
        target=found["local_id"]
    nid,stamp=identifier(),timestamp()
    if kind in workspace.NOTE_TARGETS:
        try:
            workspace.note_target(db,user,kind,target)
        except DomainError:
            pass
        else:
            field_name=workspace.NOTE_TARGETS[kind]
            db.execute(f"INSERT INTO note(id,university_id,owner_id,{field_name},body,created_at,updated_at) VALUES (?,?,?,?,?,?,?)",
                       (nid,*workspace.scope(user),target,row["body"],stamp,stamp))
            return nid
    db.execute("INSERT INTO detached_personal_note VALUES (?,?,?,?,?)",(nid,user["id"],row["title"],row["body"],stamp))
    return nid


def install_event(db,user,row):
    eid=workspace.add_event(db,user,(row["title"],row["description"],row["location"],row["starts_at"],
                                     row["ends_at"],int(row["all_day"]),row["repeat_until"] or None))
    db.executemany("INSERT INTO personal_event_skip VALUES (?,?)",[(eid,day) for day in row["skipped"]])
    return eid


def finish(db,user,pid,action,key):
    def apply():
        row=draft(db,user,pid)
        if row["state"]=="applied" and action=="apply":
            return pid
        if row["state"]!="draft":
            raise DomainError(409,"Этот перенос уже завершён.")
        if action=="cancel":
            db.execute("UPDATE personal_restore SET state='cancelled' WHERE id=?",(pid,))
            return pid
        data=json.loads(row["payload_json"])
        review=compare(db,user,data,bool(row["restore_settings"]))
        if review!=json.loads(row["review_json"]):
            raise DomainError(409,"Личные данные изменились после предпросмотра. Проверь файл заново.")
        subjects={item["id"] for item in workspace.subjects(db,user)}
        for kind in FORMATS:
            for record in data[kind]:
                external=record["id"]
                if review[kind][external]!="new":
                    continue
                if kind=="tasks":
                    local_id=install_task(db,user,record,subjects)
                elif kind=="notes":
                    local_id=install_note(db,user,record)
                else:
                    local_id=install_event(db,user,record)
                db.execute("INSERT INTO personal_restore_receipt VALUES (?,?,?,?,?)",
                           (user["id"],kind,external,local_id,fingerprint(record)))
        if row["restore_settings"]:
            config=data["settings"]
            reminders.save_settings_in_transaction(db,user,{kind for kind,enabled in config["reminders"].items() if enabled})
            profile_tools.set_preview_in_transaction(db,user,config["hide_preview"])
        db.execute("UPDATE personal_restore SET state='applied' WHERE id=?",(pid,))
        return pid
    return command(db,user,"profile.restore."+action,key,[pid],apply)
