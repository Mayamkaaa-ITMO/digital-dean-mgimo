from collections import Counter
import json

from . import personal_transfer, profile_tools, public_views
from .db import read_snapshot
from .domain import role
from .ui import back, button, e, empty, form, hidden, input_field, link, local_label, page, panel, select_field, textarea


def sessions(request,db):
    rows=profile_tools.sessions(db,request.user,request.session)
    body=""
    for row in rows:
        status="Текущий сеанс" if row["current"] else "Другой сеанс"
        remove="" if row["current"] else form(request,f'/profile/sessions/{row["id"]}/revoke',button("Отозвать"))
        expiry=local_label(row["expires_at"],request.user["timezone"])
        body+=f'<li><div><strong>{status}</strong><p>Истекает {e(expiry)}</p>{remove}</div></li>'
    active = panel("Доступ к кабинету", f'<ul class="rows">{body}</ul>'
                   + form(request, "/profile/sessions/revoke-others", button("Завершить другие сеансы")))
    rows = ""
    for item in profile_tools.devices(db, request.user):
        current = "Это устройство" if item["id"] == request.user["device_id"] else "Устройство"
        expiry = local_label(item["expires_at"], request.user["timezone"])
        remove = form(request, f'/profile/devices/{item["id"]}/revoke', button("Удалить и завершить сеансы"))
        rows += f'<li><div><strong>{current}</strong><p>Вход сохранён до {e(expiry)}</p>{remove}</div></li>'
    saved = f'<ul class="rows">{rows}</ul>' if rows else empty("Сохранённых устройств пока нет.")
    destination = back("/staff", "В кабинет сотрудника") if request.user["role"] == "staff" else back("/profile", "К профилю")
    return page(request, "Активные сеансы", destination + active + panel("Личные устройства", saved))


def privacy(request,db):
    from .personal_data import CONSENT

    role(request.user,"student")
    checked=" checked" if profile_tools.hidden_preview(db,request.user) else ""
    field=f'<label class="check"><input type="checkbox" name="hide" value="1"{checked}>Скрывать значения оценок и начислений в уведомлениях</label>'
    body=back("/profile","К профилю")+form(request,"/profile/privacy",field+button("Сохранить"))
    return page(request,"Приватность",body+link("/notifications","Типы напоминаний")
                +f'<p>{link(CONSENT,"Согласие на обработку персональных данных")}</p>')


def links(request,db):
    role(request.user,"student")
    body=back("/profile","К профилю")
    items=""
    for row in profile_tools.links(db,request.user):
        remove=form(request,f'/profile/links/{row["id"]}/remove',button("Убрать"))
        items+=f'<li><div>{link(row["url"],row["title"])}{remove}</div></li>'
    body+=panel("Сохранённые ссылки",f'<ul class="rows">{items}</ul>' if items else empty("Ссылок пока нет."))
    options=[(ident,title) for ident,title,_ in profile_tools.service_options(db,request.user,request.public)]
    if options:
        fields=hidden("kind","service")+select_field("service","Сервис",options)
        body+=panel("Сервис",form(request,"/profile/links",fields+button("В избранное"),command=True))
    fields=hidden("kind","personal")+input_field("title","Название",options='maxlength="100" required')
    fields+=input_field("url","HTTPS-ссылка",options='maxlength="320" required',kind="url")
    body+=panel("Личная ссылка",form(request,"/profile/links",fields+button("Добавить"),command=True))
    return page(request, "Сохранённые ссылки", body, "Собери здесь учебные материалы и сервисы, которыми часто пользуешься.")


def support(request,db):
    user=request.user
    owner=not request.path.startswith("/staff/")
    role(user,"student" if owner else "staff")
    root="/support" if owner else "/staff/support"
    body=""
    if owner:
        hint="Обращение попадёт в очередь сотрудника твоей демо-группы. Включение контекста добавит только указанный раздел и текущую группу."
        fields=input_field("title","Тема",options='maxlength="120" required')
        fields+=textarea("body","Описание",1800,required=True)
        fields+=select_field("topic","Раздел",list(profile_tools.SUPPORT_TOPICS.items()))
        context=public_views.scope_label(request.public) if request.public else "Учебное демо"
        fields+=f'<label class="check"><input type="checkbox" name="context" value="1">Добавить текущий контекст · {e(context)}</label>'
        body+=panel("Новое обращение",f'<p>{e(hint)}</p>'+form(request,root,fields+button("Создать"),command=True))
    items=""
    for item in profile_tools.tickets(db,user):
        state=profile_tools.SUPPORT_STATES[item["state"]]
        items+=f'<li><div>{link(root+"/"+item["id"],item["title"])}<p>{state} · {e(item["id"][:8])}</p></div></li>'
    body+=panel("Обращения",f'<ul class="rows">{items}</ul>' if items else empty("Обращений пока нет."))
    return page(request,"Поддержка",body)


def ticket(request,db,tid):
    user=request.user
    role(user,"staff" if request.path.startswith("/staff/") else "student")
    row=profile_tools.tickets(db,user,tid)
    root="/staff/support" if user["role"]=="staff" else "/support"
    state=profile_tools.SUPPORT_STATES[row["state"]]
    body=back(root,"К обращениям")+panel("Обращение",f'<p class="meta">Номер {e(tid[:8])} · {state}</p>'
                  f'<p>{e(row["context"])}</p><p class="multiline">{e(row["body"])}</p>')
    if row["reply"]:
        body += panel("Ответ сотрудника", f'<p class="meta">{e(row["reply_author"])} · '
                      f'{e(local_label(row["replied_at"],user["timezone"]))}</p><p class="multiline">{e(row["reply"])}</p>')
    if user["role"]=="staff" and row["state"]!="closed":
        update="review" if row["state"]=="open" else "closed"
        body+=form(request,root+"/"+tid,hidden("version",row["version"])+hidden("state",update)
                   +(textarea("reply", "Ответ студенту", 1800, required=True) if update=="closed" else "")
                   +button("Взять в работу" if update=="review" else "Ответить и закрыть"),command=True)
    return page(request,row["title"],body)


def data_index(request,db):
    role(request.user,"student")
    if request.path=="/profile/data/export":
        with read_snapshot(db):
            payload=json.dumps(personal_transfer.snapshot(db,request.user),ensure_ascii=False,indent=2).encode()
        return 200,payload,[("Content-Type","application/json; charset=utf-8"),
                           ("Content-Disposition",'attachment; filename="dean-personal.v1.json"')]
    body=back("/profile","К профилю")
    body+=panel("Резервная копия","<p>Скачай задачи, заметки, личные события и настройки, чтобы перенести их в другой кабинет.</p>"
                +link("/profile/data/export","Скачать копию (JSON)"))
    fields='<label>JSON до 2 МиБ<input type="file" name="file" accept=".json,application/json" required></label>'
    fields+=button("Сравнить записи")+ '<button formaction="/profile/data/upload/settings">Сравнить с настройками</button>'
    body+=panel("Загрузить резервную копию","<p>Проверь, что добавится в кабинет, перед сохранением.</p>"
                +form(request,"/profile/data/upload/keep",fields,command=True,multipart=True))
    items="".join(f'<li>{link("/profile/data/restore/"+row["id"],row["created_at"][:10])} · '
                  f'{e({"draft":"Черновик","applied":"Применён","cancelled":"Отменён"}[row["state"]])}</li>'
                  for row in personal_transfer.previews(db,request.user))
    if items:
        body+=panel("История переноса",f'<ul class="rows">{items}</ul>')
    notes="".join(f'<li><div><strong>{e(row["title"])}</strong><p class="multiline">{e(row["body"])}</p></div></li>'
                  for row in personal_transfer.detached(db,request.user))
    if notes:
        body+=panel("Заметки, восстановленные отдельно",f'<ul class="rows">{notes}</ul>')
    return page(request,"Перенос личных данных",body)


def data_review(request,db,pid):
    draft=personal_transfer.draft(db,request.user,pid)
    report=json.loads(draft["review_json"])
    data=json.loads(draft["payload_json"])
    labels={"tasks":"Задачи","notes":"Заметки","events":"Личные события"}
    body=back("/profile/data","К переносу")
    for kind in personal_transfer.FORMATS:
        counts=Counter(report[kind].values())
        rows=f'<p>Добавится {counts["new"]} · Повторы {counts["same"]} · Конфликты {counts["conflict"]}</p>'
        conflicts="".join(f'<li>{e(item["title"])}</li>' for item in data[kind] if report[kind][item["id"]]=="conflict")
        if conflicts:
            rows+=f'<p>Текущие записи сохранятся:</p><ul class="rows">{conflicts}</ul>'
        body+=panel(labels[kind],rows)
    if draft["restore_settings"]:
        flags=[name for name,value in data["settings"]["reminders"].items() if value]
        body+=panel("Настройки из файла",f'<p>{e(", ".join(flags) or "Напоминания отключены")}.</p>'
                    f'<p>Приватность превью: {"включена" if data["settings"]["hide_preview"] else "выключена"}.</p>')
    elif draft["state"]=="draft":
        body+=empty("Твои действующие настройки сохранятся.")
    if draft["state"]=="draft":
        path=f"/profile/data/restore/{pid}"
        body+=form(request,path+"/apply",button("Восстановить новые записи"),command=True)
        body+=form(request,path+"/cancel",button("Отменить перенос"),command=True)
    return page(request,"Предпросмотр восстановления",body,
                {"draft":"Черновик","applied":"Применён","cancelled":"Отменён"}[draft["state"]])
