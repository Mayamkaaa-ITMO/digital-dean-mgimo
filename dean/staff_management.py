"""Scoped staff operations built on the existing service and resource records."""
import json
import re

from . import activity_extensions, application_forms, applications, coursework, equipment, learning, reservations, services
from .db import write
from .domain import DomainError, field, identifier, require, role, timestamp
from .schedule import edit_time

FIELD_KINDS={"text": (120, "Текст"), "textarea": (1000, "Многострочный текст"),
             "date": (10, "Дата"), "integer": (6, "Целое число")}
FORM_KEY=re.compile(r"[a-z][a-z0-9_]{0,29}")
RESERVED_KEYS={"csrf","request_id","version","form_version"}
RESOURCE_STATES={"draft":"Черновик","published":"Опубликован","retired":"Архивирован"}


def require_group(db,user,group):
    if group not in {row["id"] for row in services.staff_groups(db,user)}:
        raise DomainError(403,"Группа вне области сотрудника.")
    return group


def manager_grant(db,login):
    with write(db):
        account=require(db.execute("SELECT * FROM account WHERE username=? AND role='staff'",(login,)).fetchone())
        if not services.staff_groups(db,account):
            raise DomainError(403,"Назначь сотруднику его учебную группу.")
        db.execute("INSERT OR IGNORE INTO staff_instructor_manager VALUES (?)",(account["id"],))


def can_assign(db,user):
    role(user,"staff")
    return db.execute("SELECT 1 FROM staff_instructor_manager WHERE account_id=?",(user["id"],)).fetchone() is not None


def instructor_options(db,user):
    if not can_assign(db,user):
        raise DomainError(403,"Назначение преподавателей доступно ответственному сотруднику.")
    actors=db.execute("SELECT id,full_name FROM account WHERE university_id=? AND role='staff' ORDER BY full_name",
                      (user["university_id"],))
    return [(actor["id"], actor["full_name"], row) for actor in actors for row in coursework.eligible(db,user,actor["id"])]


def assign_instructor(db,user,raw,key):
    if not can_assign(db,user):
        raise DomainError(403,"Назначение преподавателей доступно ответственному сотруднику.")
    values=tuple(raw.split("|"))
    if len(values)!=4:
        raise DomainError(422,"Выбери преподавателя, группу, семестр и дисциплину.")

    def apply():
        staff,*scope=values
        require(db.execute("SELECT id FROM account WHERE id=? AND university_id=? AND role='staff'",
                           (staff,user["university_id"])).fetchone())
        coursework.grant_in_transaction(db,user,staff,tuple(scope))
        return staff
    return services.command(db,user,"staff.instructor",key,values,apply)


def service_rows(db,user,sid=None):
    role(user,"staff")
    rows=db.execute("""
        SELECT s.*,m.group_id,m.department,m.active,m.version,m.form_json,g.name AS group_name
        FROM managed_service m JOIN service s ON s.id=m.service_id AND s.university_id=m.university_id
        JOIN staff_group sg ON sg.university_id=m.university_id AND sg.group_id=m.group_id AND sg.staff_id=?
        JOIN study_group g ON g.university_id=m.university_id AND g.id=m.group_id
        WHERE m.university_id=? AND m.owner_id=? ORDER BY s.name,s.id
    """,(user["id"],user["university_id"],user["id"]))
    if sid is not None:
        return require(next((row for row in rows if row["id"]==sid),None))
    return list(rows)


def specs(raw):
    entries=[]
    keys=set()
    for line in raw.strip().splitlines():
        parts=[part.strip() for part in line.split("|")]
        if len(parts)!=4:
            raise DomainError(422,"Строка поля: ключ | подпись | тип | 0 или 1.")
        name,label,kind,required=parts
        if (not FORM_KEY.fullmatch(name) or name in RESERVED_KEYS or name in keys
                or kind not in FIELD_KINDS or required not in {"0","1"}):
            raise DomainError(422,"Укажи уникальный ключ, безопасный тип и признак обязательности.")
        label=field(label,"Подпись поля",80)
        entries.append(application_forms.text(name,label,FIELD_KINDS[kind][0],int(required),kind))
        keys.add(name)
    if not 1<=len(entries)<=6:
        raise DomainError(422,"Конструктор поддерживает от одного до шести полей.")
    return entries


def save_service(db,user,fields,key,sid=None):
    role(user,"staff")
    title=field(fields.get("title",""),"Название",120)
    description=field(fields.get("description",""),"Описание",600)
    department=field(fields.get("department",""),"Подразделение",120)
    category=fields.get("category")
    days=learning.optional_int(fields.get("target_days"),"Ожидаемый срок",90)
    if category not in application_forms.CATEGORIES or not days:
        raise DomainError(422,"Выбери категорию и срок от одного до девяноста дней.")
    value=specs(fields.get("fields",""))
    active=int(fields.get("active")=="1")

    def apply():
        if sid:
            old=service_rows(db,user,sid)
            applications.version_check(old,fields.get("version"))
            code,group,version=sid,old["group_id"],old["version"]+1
            db.execute("""
                UPDATE service SET name=?,description=?,category=?,target_days=? WHERE university_id=? AND id=?
            """,(title,description,category,days,user["university_id"],code))
        else:
            code,group,version="managed-"+identifier(),require_group(db,user,fields.get("group")),1
            db.execute("""
                INSERT INTO service(university_id,id,name,description,placeholder,category,target_days)
                VALUES (?,?,?,?,'',?,?)
            """,(user["university_id"],code,title,description,category,days))
        schema={"version":version,"service":code,"name":title,"target_days":days,"document_kind":None,
                "fields":value,"result":"","required_file":False}
        doc=json.dumps(schema,ensure_ascii=False)
        db.execute("""
            INSERT INTO managed_service VALUES (?,?,?,?,?,?,?,?)
            ON CONFLICT(university_id,service_id) DO UPDATE SET department=excluded.department,
                active=excluded.active,version=excluded.version,form_json=excluded.form_json
        """,(user["university_id"],code,group,user["id"],department,active,version,doc))
        details=json.dumps({"form":schema,"department":department,"active":active,"description":description,"category":category},
                           ensure_ascii=False)
        db.execute("INSERT INTO managed_service_revision VALUES (?,?,?,?,?,?)",
                   (user["university_id"],code,version,user["id"],details,timestamp()))
        return code

    return services.command(db,user,"management.service",key,[sid,fields],apply)


def scoped_offer(db,user,bid):
    row=reservations.offer(db,user,bid,manage=True)
    info=require(db.execute("SELECT * FROM managed_offer WHERE bookable_id=?",(bid,)).fetchone())
    require_group(db,user,info["group_id"])
    return row,info


def slot_bounds(user,fields,kind):
    start=timestamp(edit_time(fields.get("starts",""),user["timezone"]))
    end=timestamp(edit_time(fields.get("ends",""),user["timezone"]))
    capacity=learning.optional_int(fields.get("capacity"),"Вместимость",1000)
    if start>=end or end<=timestamp() or not capacity or (kind in {"room","consultation"} and capacity!=1):
        raise DomainError(422,"Укажи открываемое окно в будущем и допустимую вместимость. Для помещения и консультации одно место.")
    return start,end,capacity


def offer_history(db,user,bid,version,action,details):
    db.execute("""
        INSERT INTO managed_offer_history(bookable_id,actor_id,version,action,details_json,created_at)
        VALUES (?,?,?,?,?,?)
    """,(bid,user["id"],version,action,json.dumps(details,ensure_ascii=False),timestamp()))


def create_resource(db,user,fields,key):
    role(user,"staff")
    kind=fields.get("kind")
    if kind not in {*reservations.KINDS,"equipment"}:
        raise DomainError(422,"Выбери вид ресурса.")
    title=field(fields.get("title",""),"Название",120)
    activity = kind == "activity" and activity_extensions.enabled()
    room = fields.get("map_room", "") if activity else ""
    place=field(activity_extensions.room_label(room, fields.get("place","")), "Место", 120)
    host = field(fields.get("host", "Организатор деканата"), "Организатор", 120) if activity else "Организатор деканата"
    if kind=="equipment":
        label=field(fields.get("label",""),"Инвентарный номер",30)
        return_place=field(fields.get("return_place",""),"Место возврата",120)
    else:
        category=field(fields.get("category",""),"Категория",80)
        rules=field(fields.get("rules",""),"Правила",600,0)
        start,end,capacity=slot_bounds(user,fields,kind)
        if activity and start <= timestamp():
            raise DomainError(422, "Начало мероприятия должно быть в будущем.")

    def apply():
        group=require_group(db,user,fields.get("group"))
        bid=identifier()
        if kind=="equipment":
            if db.execute("SELECT 1 FROM equipment_unit WHERE university_id=? AND label=?",
                          (user["university_id"],label)).fetchone():
                raise DomainError(409,"Этот инвентарный номер уже используется.")
            db.execute("""
                INSERT INTO equipment_unit(id,university_id,manager_id,label,title,pickup,return_place)
                VALUES (?,?,?,?,?,?,?)
            """,(bid,user["university_id"],user["id"],label,title,place,return_place))
            db.execute("INSERT INTO managed_equipment VALUES (?,?)",(bid,group))
            equipment.record(db,user,bid,None,"created","Демонстрационная единица техники",timestamp())
        else:
            db.execute("INSERT INTO bookable VALUES (?,?,?,?,?,?,?,?,?,?)",
                       (bid,user["university_id"],user["id"],kind,title,category,place,"",rules,host))
            db.execute("INSERT INTO managed_offer VALUES (?,?,'draft',1)",(bid,group))
            db.execute("""
                INSERT INTO reservation_slot(id,university_id,bookable_id,starts_at,ends_at,capacity,closed)
                VALUES (?,?,?,?,?,?,1)
            """,(identifier(),user["university_id"],bid,start,end,capacity))
            details = [start, end, capacity]
            if activity:
                activity_extensions.save_room(db, bid, room)
                details = {"starts_at": start, "ends_at": end, "capacity": capacity, "title": title,
                           "category": category, "place": place, "host": host, "rules": rules, "map_room": room}
            offer_history(db,user,bid,1,"draft",details)
        return bid

    return services.command(db,user,"management.resource",key,[fields],apply)


def resources(db,user):
    role(user,"staff")
    return list(db.execute("""
        SELECT b.id,b.title,b.kind,m.state FROM bookable b JOIN managed_offer m ON m.bookable_id=b.id
        JOIN staff_group sg ON sg.university_id=b.university_id AND sg.group_id=m.group_id AND sg.staff_id=?
        WHERE b.manager_id=? AND b.university_id=? ORDER BY b.title,b.id
    """,(user["id"],user["id"],user["university_id"])))


def managed_slot(db,user,bid,sid=""):
    return require(db.execute("""
        SELECT s.* FROM reservation_slot s WHERE s.university_id=? AND s.bookable_id=? AND (?='' OR s.id=?)
        ORDER BY starts_at LIMIT 1
    """,(user["university_id"],bid,sid,sid)).fetchone())


def edit_activity(db, user, record, slot, fields, version, existing):
    bid, sid = record["id"], slot["id"]
    if slot["starts_at"] <= timestamp():
        raise DomainError(409, "Начавшееся мероприятие нельзя переносить или редактировать.")
    start, end, capacity = slot_bounds(user, fields, "activity")
    if start <= timestamp():
        raise DomainError(422, "Начало мероприятия должно быть в будущем.")
    if len(existing) > capacity:
        raise DomainError(409, "Вместимость ниже числа подтверждённых записей.")
    if db.execute("""
        SELECT 1 FROM reservation_slot WHERE bookable_id=? AND id<>? AND starts_at<? AND ends_at>?
    """, (bid, sid, end, start)).fetchone():
        raise DomainError(409, "Другое окно мероприятия пересекается по времени.")
    moved = (start, end) != (slot["starts_at"], slot["ends_at"])
    if moved and db.execute("""
        SELECT 1 FROM reservation r JOIN reservation_attendance a ON a.reservation_id=r.id
        WHERE r.slot_id=? AND r.status='reserved'
    """, (sid,)).fetchone():
        raise DomainError(409, "Посещение уже подтверждено. Перенос недоступен.")
    if moved and db.execute("""
        SELECT 1 FROM reservation r JOIN reservation other
          ON other.university_id=r.university_id AND other.student_id=r.student_id
        WHERE r.slot_id=? AND r.status='reserved' AND other.slot_id<>r.slot_id
          AND other.status='reserved' AND other.starts_at<? AND other.ends_at>?
    """, (sid, end, start)).fetchone():
        raise DomainError(409, "У участника уже есть запись на это время. Выбери другой интервал.")

    room = fields.get("map_room", activity_extensions.room_key(db, bid))
    values = {
        name: field(fields.get(name, record[name]), label, limit, minimum)
        for name, label, limit, minimum in (
            ("title", "Название", 120, 1), ("category", "Категория", 80, 1),
            ("host", "Организатор", 120, 1), ("rules", "Описание", 600, 0),
        )
    }
    values["place"] = field(activity_extensions.room_label(room, fields.get("place", record["place"])), "Место", 120)
    db.execute("""
        UPDATE bookable SET title=:title,category=:category,host=:host,rules=:rules,place=:place WHERE id=:bid
    """, {**values, "bid": bid})
    activity_extensions.save_room(db, bid, room)
    db.execute("UPDATE reservation_slot SET starts_at=?,ends_at=?,capacity=?,version=version+1 WHERE id=?",
               (start, end, capacity, sid))
    if moved:
        db.execute("UPDATE reservation SET starts_at=?,ends_at=? WHERE slot_id=? AND status='reserved'",
                   (start, end, sid))
    relevant = any(values[name] != record[name] for name in ("title", "place", "host", "rules"))
    if moved or relevant:
        headline = "Мероприятие перенесено" if moved else "Детали мероприятия изменились"
        for row in existing:
            if moved:
                activity_extensions.dismiss_reminder(db, row["id"])
            services.notify(db, user["university_id"], row["student_id"], "booking", row["id"],
                            f'activity:edit:{row["id"]}:{version}', headline, values["title"], timestamp())
    if capacity > slot["capacity"]:
        reservations.alert_queue(db, reservations.slot(db, user, sid, manage=True), capacity - slot["capacity"])
    return {**values, "starts_at": start, "ends_at": end, "capacity": capacity, "map_room": room, "slot": sid}


def retire_activity(db, user, bid, version, stamp):
    future = list(db.execute("""
        SELECT r.*,b.title,a.confirmed_at FROM reservation r JOIN reservation_slot s ON s.id=r.slot_id
        JOIN bookable b ON b.id=s.bookable_id
        LEFT JOIN reservation_attendance a ON a.reservation_id=r.id
        WHERE b.id=? AND r.status='reserved' AND r.starts_at>?
    """, (bid, stamp)))
    if any(row["confirmed_at"] for row in future):
        raise DomainError(409, "Посещение уже подтверждено. Отмена недоступна.")
    if db.execute("SELECT 1 FROM reservation_slot WHERE bookable_id=? AND starts_at<=? AND ends_at>?",
                  (bid, stamp, stamp)).fetchone():
        raise DomainError(409, "Дождись окончания текущего окна.")
    for row in future:
        db.execute("UPDATE reservation SET status='cancelled',cancelled_at=? WHERE id=?", (stamp, row["id"]))
        activity_extensions.dismiss_reminder(db, row["id"])
        services.notify(db, user["university_id"], row["student_id"], "booking", row["id"],
                        f'activity:cancel:{row["id"]}:{version}', "Мероприятие отменено", row["title"], stamp)
    db.execute("""
        UPDATE reservation_wait SET status='left' WHERE status<>'left'
        AND slot_id IN (SELECT id FROM reservation_slot WHERE bookable_id=?)
    """, (bid,))
    db.execute("UPDATE reservation_slot SET closed=1,version=version+1 WHERE bookable_id=?", (bid,))
    db.execute("UPDATE managed_offer SET state='retired' WHERE bookable_id=?", (bid,))
    return {"cancelled_registrations": len(future)}


def manage_resource(db,user,bid,action,fields,key):
    def apply():
        record,info=scoped_offer(db,user,bid)
        applications.version_check(info,fields.get("version"))
        activity = record["kind"] == "activity" and activity_extensions.enabled()
        if info["state"]=="retired":
            raise DomainError(409,"Архивный ресурс сохранён в истории.")
        slot=managed_slot(db,user,bid,fields.get("slot","") if activity and action == "move" else "")
        existing=reservations.occupied(db,slot)
        stamp=timestamp()
        version=info["version"]+1
        info_values=[slot["starts_at"],slot["ends_at"],slot["capacity"]]
        if action=="publish":
            if (slot["starts_at"] if activity else slot["ends_at"])<=stamp:
                raise DomainError(422,"Сначала укажи будущее окно.")
            if activity and info["state"] != "draft":
                raise DomainError(409, "Мероприятие уже опубликовано.")
            db.execute("UPDATE managed_offer SET state='published' WHERE bookable_id=?",(bid,))
            db.execute("UPDATE reservation_slot SET closed=0,version=version+1 WHERE id=?",(slot["id"],))
        elif activity and action == "move":
            info_values = edit_activity(db, user, record, slot, fields, version, existing)
        elif activity and action == "retire":
            info_values = retire_activity(db, user, bid, version, stamp)
        elif action=="move":
            start,end,capacity=slot_bounds(user,fields,record["kind"])
            if slot["starts_at"]<=stamp and existing:
                raise DomainError(409,"Изменение уже начавшихся подтверждённых записей требует отдельного решения.")
            if len(existing)>capacity:
                raise DomainError(409,"Вместимость ниже числа подтверждённых записей.")
            moved=(start,end)!=(slot["starts_at"],slot["ends_at"])
            if moved and existing and record["kind"]!="activity":
                raise DomainError(409,"Сохрани интервалы подтверждённых записей.")
            db.execute("UPDATE reservation_slot SET starts_at=?,ends_at=?,capacity=?,version=version+1 WHERE id=?",
                       (start,end,capacity,slot["id"]))
            if moved and existing:
                db.execute("UPDATE reservation SET starts_at=?,ends_at=? WHERE slot_id=? AND status='reserved'",
                           (start,end,slot["id"]))
                for item in existing:
                    services.notify(db,user["university_id"],item["student_id"],"booking",item["id"],
                                    f'activity:move:{item["id"]}:{version}',"Мероприятие перенесено",
                                    record["title"],stamp)
            info_values.append([start,end,capacity])
        else:
            if existing and (record["kind"]!="activity" or slot["starts_at"]<=stamp):
                raise DomainError(409,"Есть подтверждённые записи. Архивирование пока недоступно.")
            if existing:
                db.execute("UPDATE reservation SET status='cancelled',cancelled_at=? WHERE slot_id=? AND status='reserved'",
                           (stamp,slot["id"]))
                for item in existing:
                    services.notify(db,user["university_id"],item["student_id"],"booking",item["id"],
                                    f'activity:cancel:{item["id"]}:{version}',"Мероприятие отменено",
                                    record["title"],stamp)
            db.execute("UPDATE reservation_wait SET status='left' WHERE slot_id=? AND status<>'left'",(slot["id"],))
            db.execute("UPDATE reservation_slot SET closed=1,version=version+1 WHERE bookable_id=?",(bid,))
            db.execute("UPDATE managed_offer SET state='retired' WHERE bookable_id=?",(bid,))
        db.execute("UPDATE managed_offer SET version=? WHERE bookable_id=?",(version,bid))
        offer_history(db,user,bid,version,action,info_values)
        return bid
    return services.command(db,user,"management.resource."+action,key,[bid,fields],apply)
