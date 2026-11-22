import json

from . import activity_extensions, application_forms, coursework, equipment, reservations, services, staff_management as manage
from .domain import timestamp
from .schedule import local_value
from .ui import back, button, e, empty, form, hidden, input_field, link, local_label, page, panel, select_field, textarea


def group_options(db,user):
    return [(row["id"],row["name"]) for row in services.staff_groups(db,user)]


def hub(request,db):
    user=request.user
    services_rows=manage.service_rows(db,user)
    managed=manage.resources(db,user)
    assignments=coursework.assignment(db,user)
    waiting=sum(item["status"] in {"review","submitted"} for row in assignments for item in coursework.submissions(db,user,row["id"]))
    problem=sum(bool(row["maintenance"]) for row in equipment.units(db,user,manage=True))
    body=back("/staff","К кабинету сотрудника")
    body+=panel("Рабочая сводка",f"<p>Ответов к проверке: {waiting}. Единиц техники на обслуживании: {problem}.</p>",
                link("/staff/work","Учебные работы"))
    items = "".join(
        f'<li><div>{link("/staff/manage/services/" + row["id"], row["name"])}'
        f'<p class="meta">{e(row["group_name"])} · версия {row["version"]} · '
        f'{"приём открыт" if row["active"] else "архив"}</p></div></li>'
        for row in services_rows
    )
    body+=panel("Услуги",f'<ul class="rows">{items}</ul>' if items else empty("Собственных услуг пока нет."),
                link("/staff/manage/services/new","Добавить услугу"))
    items = "".join(
        f'<li><div>{link("/staff/manage/resources/" + row["id"], row["title"])}'
        f'<p class="meta">{e(manage.RESOURCE_STATES[row["state"]])}</p></div></li>'
        for row in managed
    )
    actions = link("/staff/manage/resources/new", "Создать")
    if activity_extensions.enabled():
        actions += link("/staff/manage/resources/new?kind=activity", "Создать мероприятие")
    body+=panel("Ресурсы и мероприятия", f'<ul class="rows">{items}</ul>' if items else "",
                f'<div class="switcher">{actions}</div>')
    if manage.can_assign(db,user):
        options=[]
        for owner,name,row in manage.instructor_options(db,user):
            value="|".join((owner,row["group_id"],row["period_id"],row["subject_id"]))
            options.append((value,name+" · "+row["group_name"]+" · "+row["subject"]))
        fields=select_field("assignment","Преподаватель и область",options)
        body+=panel("Назначить преподавателя",form(request,"/staff/manage/instructors",fields+button("Назначить"),command=True))
    return page(request,"Управление услугами и ресурсами",body)


def service_form(request,db,sid=None):
    user=request.user
    row=manage.service_rows(db,user,sid) if sid else {"name":"","description":"","department":"","category":"study",
                                                       "target_days":3,"active":1,"form_json":None}
    path="/staff/manage/services/"+(sid or "new")
    fields=hidden("version",row["version"]) if sid else select_field("group","Группа",group_options(db,user))
    fields+=input_field("title","Название",row["name"],'required maxlength="120"')
    fields+=textarea("description","Описание",600,True,value=row["description"])
    fields+=input_field("department","Ответственное подразделение",row["department"],'required maxlength="120"')
    fields+=select_field("category","Категория",list(application_forms.CATEGORIES.items()),row["category"])
    fields+=input_field("target_days","Срок в днях",row["target_days"],'required min="1" max="90"',"number")
    specs=json.loads(row["form_json"])["fields"] if row["form_json"] else []
    value="\n".join(f'{item["key"]} | {item["label"]} | {item["kind"]} | {int(bool(item["minimum"]))}' for item in specs)
    fields+=textarea("fields","Ключ | подпись | тип | обязательность (0 или 1)",2000,True,value=value)
    fields+=f'<label class="check"><input type="checkbox" name="active" value="1"{" checked" if row["active"] else ""}>Показывать и принимать заявки</label>'
    hint="<p>Типы полей: text, textarea, date, integer. Снятый приём сохраняет прежние заявления и их историю.</p>"
    body=back("/staff/manage","К управлению")+hint+form(request,path,fields+button("Сохранить версию"),command=True)
    return page(request,"Услуга",body)


def event_fields(row, slot, room, zone, values=None):
    values = values or {}
    row = {**row, **values}
    values = [
        ("title", "Название", 120), ("category", "Категория", 80), ("host", "Организатор", 120),
    ]
    fields = "".join(input_field(name, label, row.get(name, ""), f'maxlength="{maximum}" required')
                     for name, label, maximum in values)
    fields += textarea("rules", "Описание", 600, value=row.get("rules", ""))
    fields += input_field("place", "Аудитория или адрес", row.get("place", ""), 'maxlength="120"')
    fields += select_field("map_room", "Аудитория на карте кампуса",
                           [("", "Указать место текстом"), *activity_extensions.rooms().items()], row.get("map_room", room))
    fields += '<p class="meta">Если выбрать аудиторию на карте, её адрес появится в афише и календаре.</p>'
    for name, label, column in (("starts", "Начало", "starts_at"), ("ends", "Окончание", "ends_at")):
        saved = slot.get(column)
        value = row.get(name, local_value(saved, zone).strftime("%Y-%m-%dT%H:%M") if saved else "")
        fields += input_field(name, label, value, "required", "datetime-local")
    capacity = row.get("capacity", slot.get("capacity", 1))
    return fields + input_field("capacity", "Вместимость", capacity, 'required min="1" max="1000"', "number")


def event_error(message):
    return f'<p class="change" role="alert" tabindex="-1" data-restore-focus>{e(message)}</p>' if message else ""


def resource_form(request,db, *, problem="", values=None):
    values = values or {}
    groups=group_options(db,request.user)
    fields=select_field("group","Группа",groups,values.get("group",""))
    if activity_extensions.enabled() and "activity" in {request.query("kind"), values.get("kind")}:
        fields += hidden("kind", "activity") + event_fields({}, {}, "", request.user["timezone"], values)
        body = back("/staff/manage", "К управлению") + event_error(problem)
        body += form(request, "/staff/manage/resources/new", fields + button("Сохранить черновик"), command=True)
        return page(request, "Новое мероприятие", body)
    fields+=select_field("kind","Вид",[*reservations.KINDS.items(),("equipment","Техника")])
    fields+=input_field("title","Название",options='maxlength="120" required')
    fields+=input_field("place","Место или стойка выдачи",options='maxlength="120" required')
    fields+=input_field("category","Категория",options='maxlength="80"')
    fields+=textarea("rules","Правила для окон",600)
    fields+=input_field("starts","Начало окна",kind="datetime-local")
    fields+=input_field("ends","Конец окна",kind="datetime-local")
    fields+=input_field("capacity","Вместимость",1,'min="1" max="1000"',"number")
    fields+=input_field("label","Инвентарный номер техники",options='maxlength="30"')
    fields+=input_field("return_place","Место возврата техники",options='maxlength="120"')
    intro=("<p>Для техники укажи инвентарный номер и место возврата. "
           "Для остальных видов укажи категорию, время начала и конца и вместимость.</p>")
    return page(request,"Новый ресурс",back("/staff/manage","К управлению")+intro
                +form(request,"/staff/manage/resources/new",fields+button("Создать"),command=True))


def resource_detail(request,db,bid, *, problem="", values=None):
    user=request.user
    row,info=manage.scoped_offer(db,user,bid)
    if activity_extensions.enabled() and row["kind"] == "activity":
        return activity_detail(request, db, row, info, problem, values or {})
    slot=manage.managed_slot(db,user,bid)
    path="/staff/manage/resources/"+bid
    body=back("/staff/manage","К управлению")
    when=local_label(slot["starts_at"],user["timezone"])+" — "+local_label(slot["ends_at"],user["timezone"])
    body+=panel("Окно",f'<p>{e(when)} · {e(row["place"])} · вместимость {slot["capacity"]}.</p>')
    version=hidden("version",info["version"])
    if info["state"]!="retired":
        if info["state"]=="draft":
            body+=form(request,path+"/publish",version+button("Опубликовать"),command=True)
        dates=version
        for name,label,column in (("starts","Начало","starts_at"),("ends","Конец","ends_at")):
            dates+=input_field(name,label,local_value(slot[column],user["timezone"]).strftime("%Y-%m-%dT%H:%M"),
                               "required","datetime-local")
        dates+=input_field("capacity","Вместимость",slot["capacity"],'required min="1" max="1000"',"number")
        body+=panel("Часы и вместимость",form(request,path+"/move",dates+button("Сохранить изменения"),command=True))
        body+=form(request,path+"/retire",version+button("Отменить и архивировать"),command=True)
    history="".join(f'<li>Версия {item["version"]} · {e(item["action"])} · {e(item["created_at"])}</li>'
                    for item in db.execute("SELECT * FROM managed_offer_history WHERE bookable_id=? ORDER BY version DESC",(bid,)))
    body+=panel("История",f'<ul class="rows">{history}</ul>')
    if info["state"]=="published":
        body+=link("/staff/slots/"+slot["id"],"Открыть окно записи")
    return page(request,row["title"],body,manage.RESOURCE_STATES[info["state"]])


def activity_detail(request, db, row, info, problem, values):
    user, bid = request.user, row["id"]
    slots = reservations.slots(db, user, bid, upcoming=False)
    selected = request.query("slot") or values.get("slot", "")
    slot = manage.managed_slot(db, user, bid, selected) if selected else next(
        (item for item in slots if item["starts_at"] > timestamp()), slots[0])
    path = "/staff/manage/resources/" + bid
    version = hidden("version", info["version"])
    status = {"draft": "Черновик", "published": "Опубликовано", "retired": "Закрыто для регистрации"}[info["state"]]
    body = back("/staff/manage", "К управлению") + event_error(problem)
    when = local_label(slot["starts_at"], user["timezone"]) + " — " + local_label(slot["ends_at"], user["timezone"])
    registered = len(reservations.occupied(db, slot))
    info_text = f'<p>{e(when)}</p><p>{e(row["place"])}</p><p>Организатор: {e(row["host"])}</p>'
    info_text += f'<p>Регистраций: {registered} из {slot["capacity"]}.</p>'
    if len(slots) > 1:
        info_text += '<ul class="rows">' + "".join(
            f'<li>{link(path + "?slot=" + item["id"], local_label(item["starts_at"], user["timezone"]))}</li>'
            for item in slots) + "</ul>"
    actions = ""
    if info["state"] == "published" or (info["state"] == "retired" and registered):
        actions = link("/staff/slots/" + slot["id"], "Открыть список участников")
    if info["state"] == "draft":
        actions = form(request, path + "/publish", version + button("Опубликовать"), command=True)
    body += panel("Мероприятие", info_text, actions)
    if info["state"] != "retired":
        if slot["starts_at"] > timestamp():
            fields = version + hidden("slot", slot["id"]) + event_fields(
                dict(row), dict(slot), activity_extensions.room_key(db, bid), user["timezone"],
                values if values.get("version") == str(info["version"]) else None)
            body += panel("Редактирование", form(request, path + "/move", fields + button("Сохранить изменения"), command=True))
        title = "Отменить мероприятие" if info["state"] == "published" else "Архивировать черновик"
        text = ("Будущие регистрации будут отменены. Участники получат уведомление."
                if info["state"] == "published" else "Черновик останется доступен в истории.")
        body += f'<details class="panel"><summary>{e(title)}</summary><p>{e(text)}</p>'
        body += form(request, path + "/retire", version + button(title), command=True) + "</details>"
    actions = {"draft": "Создан черновик", "publish": "Публикация", "move": "Правка",
               "retire": "Отмена или архивирование"}
    history = ""
    for item in db.execute("""
        SELECT h.*,a.full_name FROM managed_offer_history h JOIN account a ON a.id=h.actor_id
        WHERE h.bookable_id=? ORDER BY h.version DESC
    """, (bid,)):
        detail = json.loads(item["details_json"])
        label = actions[item["action"]]
        note = ""
        if isinstance(detail, dict) and "starts_at" in detail:
            note = (f'{detail["title"]} · {local_label(detail["starts_at"], user["timezone"])}'
                    f' · {detail["place"]} · вместимость {detail["capacity"]}')
        if isinstance(detail, dict) and "cancelled_registrations" in detail:
            note = f'Отменено регистраций: {detail["cancelled_registrations"]}'
        history += (f'<li><div><strong>{label}</strong> · версия {item["version"]}'
                    f'<p class="meta">{e(item["full_name"])} · {e(local_label(item["created_at"], user["timezone"]))}</p>'
                    f'<p>{e(note)}</p></div></li>')
    body += panel("История изменений", f'<ul class="rows">{history}</ul>')
    return page(request, row["title"], body, status)
