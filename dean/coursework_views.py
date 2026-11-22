import json

from . import coursework as work
from .domain import timestamp
from .schedule import local_value
from .ui import back, button, e, empty, form, hidden, input_field, link, linked_row, local_label, page, panel, select_field, textarea


def teacher_index(request,db):
    from . import lecture_studio

    assignments=work.assignment(db,request.user)
    rows=""
    for row in assignments:
        pending=sum(item["status"] in {"submitted","review"} for item in work.submissions(db,request.user,row["id"]))
        rows+=linked_row("/staff/work/"+row["id"],row["title"],f'{work.STATES[row["state"]]} · Проверить: {pending}')
    actions = link("/staff/work/new", "Создать работу")
    if lecture_studio.available(request.user):
        actions += " · " + link(lecture_studio.PATH, "Lecture Studio")
    return page(request,"Учебные работы",panel("Мои дисциплины",f'<ul class="rows">{rows}</ul>' if rows else
                empty("Работ пока нет. Создание доступно для назначенных дисциплин."),
                actions))


def assignment_form(request,db,aid=None):
    scopes=work.teacher_scopes(db,request.user)
    empty_values={"title":"","description":"","response_mode":"either","attempt_limit":2,"late_allowed":0,"rubric_json":"[]"}
    row=work.assignment(db,request.user,aid) if aid else empty_values
    path="/staff/work/"+aid+"/edit" if aid else "/staff/work/new"
    fields=hidden("version",row["version"]) if aid else select_field("scope","Группа и предмет",
        [("|".join((item["group_id"],item["period_id"],item["subject_id"])),item["group_name"]+" · "+item["subject"])
         for item in scopes])
    fields+=input_field("title","Название",row["title"],'maxlength="120" required')
    fields+=textarea("description","Условия",2000,True,value=row["description"])
    when=local_value(row["due_at"],request.user["timezone"]).strftime("%Y-%m-%dT%H:%M") if aid else ""
    fields+=input_field("due","Срок",when,"required","datetime-local")
    fields+=select_field("response_mode","Формат",list(work.MODES.items()),row["response_mode"])
    fields+=input_field("attempt_limit","Попытки",row["attempt_limit"],'min="1" max="8" required',"number")
    fields+=f'<label class="check"><input type="checkbox" name="late" value="1"{" checked" if row["late_allowed"] else ""}>Принимать после срока</label>'
    rubric="\n".join(f"{name} | {maximum}" for name,maximum in json.loads(row["rubric_json"]))
    fields+=textarea("rubric","Один критерий на строку: название | максимум",1000,True,value=rubric)
    body=back("/staff/work","К работам")
    if not aid and not scopes:
        body+=empty("Назначение преподавателя на группу и дисциплину ещё отсутствует.")
    else:
        body+=form(request,path,fields+button("Сохранить условия"),command=True)
    return page(request,"Условия учебной работы",body)


def rules(request,row,revision):
    when=local_label(row["due_at"],request.user["timezone"])
    items="".join(f"<li>{e(name)} · {points}</li>" for name,points in json.loads(row["rubric_json"]))
    details=(f'<p class="multiline">{e(row["description"])}</p><p>Срок: {e(when)} · '
             f'{e(work.MODES[row["response_mode"]])} · попыток: {row["attempt_limit"]} · максимум: {row["max_points"]}.</p>'
             f'<p>Поздняя сдача {"разрешена" if row["late_allowed"] else "закрыта"}. Условия, версия {revision}.</p>'
             f'<ul class="rows">{items}</ul>')
    return panel("Правила работы",details)


def assignment_detail(request,db,aid):
    row=work.assignment(db,request.user,aid)
    path="/staff/work/"+aid
    body=back("/staff/work","К работам")+rules(request,row,row["definition_version"])
    if row["state"]!="archived":
        body+=link(path+"/edit","Создать версию условий")
        action="publish" if row["state"]=="draft" else "archive"
        body+=form(request,path+"/"+action,hidden("version",row["version"])
                   +button("Опубликовать группе" if action=="publish" else "Снять с публикации"),command=True)
    rows=work.submissions(db,request.user,aid)
    items="".join(f'<li><div>{link("/staff/work/review/"+item["id"],item["student_name"])}'
                   f'<p>{work.WORK_STATES[item["status"]]}</p></div></li>' for item in rows)
    body+=panel("Очередь ответов",f'<ul class="rows">{items}</ul>' if items else empty("Опубликованных ответов пока нет."))
    return page(request,row["title"],body,work.STATES[row["state"]])


def student_work_rows(rows, zone, *, show_subject=False):
    items = []
    for row in rows:
        state = work.student_status(row)
        due = local_label(row["due_at"], zone)
        details = " · ".join(filter(None, (row["subject"] if show_subject else "", state, f"Срок {due}")))
        items.append(linked_row("/work/" + row["id"], row["title"], details))
    return f'<ul class="rows">{"".join(items)}</ul>'


def student_index(request,db):
    rows=work.student_list(db,request.user)
    content = student_work_rows(rows, request.user["timezone"], show_subject=True) if rows else empty(
        "Учебных работ пока нет. Они появятся после публикации преподавателем. "
        "Компоненты оценки и их статусы доступны в карточках дисциплин.") + link("/grades", "К успеваемости")
    return page(request,"Мои учебные работы",panel("Назначено группе",content))


def attempt_list(request,db,wid,*,staff=False):
    output=[]
    for row in work.attempts(db,wid,staff=staff):
        when=local_label(row["submitted_at"],request.user["timezone"])
        text=f'<p class="meta">{e(when)} · версия условий {row["definition_version"]}'+(" · после срока" if row["late"] else "")+"</p>"
        text+=f'<p class="multiline">{e(row["response"])}</p>'
        if row["file_id"]:
            text+=link("/work/files/"+row["file_id"],row["filename"])
        if row["decision"] in {"returned","graded"}:
            text+=f'<p class="multiline">{e(row["feedback"])}</p>'
            if row["decision"]=="graded":
                text+=f'<p>Опубликованный результат: {sum(json.loads(row["points_json"]))} баллов.</p>'
        output.append(panel(f'Попытка {row["number"]}',text))
    return "".join(output)


def student_detail(request,db,wid):
    row=work.student_list(db,request.user,wid)
    path="/work/"+wid
    body=back("/work","К моим работам")
    result = row["result_id"]
    state = work.student_status(row)
    body+=panel("Статус работы",f'<p><strong>{e(state)}</strong></p><p>{e(work.student_next_step(row))}</p>'
                + (link("/grades/"+result,"К результату дисциплины") if result else ""))
    body+=rules(request,row,row["rules_version"])
    if row["assignment_state"]=="published" and row["status"] in {"draft","returned"}:
        version=hidden("version",work.answer_version(row))
        fields=version+textarea("answer","Твой ответ",2400,value=row["draft_text"])
        controls=button("Сохранить черновик")
        if work.accepts_submission_at(row, timestamp()):
            controls+=f'<button formaction="{path}/submit">Отправить</button>'
        body+=panel("Ответ",form(request,path+"/draft",fields+controls,command=True))
        if row["response_mode"]!="text":
            file=work.draft_file(db,wid)
            upload='<label>TXT или PNG до 2 МиБ<input type="file" name="file" accept=".txt,.png" required></label>'
            text=form(request,path+"/attachment",version+upload+button("Прикрепить"),command=True,multipart=True)
            if file:
                text=f'<p>{link("/work/files/"+file["id"],file["name"])}</p>'+text
                text+=form(request,path+"/attachment",version+button("Убрать черновой файл"),command=True)
            body+=panel("Черновое вложение",text)
    body+=attempt_list(request,db,wid)
    return page(request,row["title"],body)


def review_form(request,db,wid):
    row=work.teacher_work(db,request.user,wid)
    body=back("/staff/work/"+row["assignment_id"],"К очереди работы")+rules(request,row,row["rules_version"])
    body+=attempt_list(request,db,wid,staff=True)
    attempts=work.attempts(db,wid,staff=True)
    if row["status"] in {"submitted","review"} and attempts:
        latest=attempts[0]
        path="/staff/work/review/"+wid
        fields=hidden("version",row["version"])+hidden("attempt",latest["id"])
        criteria=json.loads(row["rubric_json"])
        points=json.loads(latest["points_json"]) if latest["points_json"] else [None]*len(criteria)
        for index,(name,maximum) in enumerate(criteria):
            fields+=input_field("points_"+str(index),name,points[index] if points[index] is not None else "",
                                f'min="0" max="{maximum}"',"number")
        fields+=textarea("feedback","Обратная связь",1800,value=latest["feedback"] or "")
        actions=(button("Сохранить черновик проверки")
                 +f'<button formaction="{path}/return">Вернуть на доработку</button>'
                 +f'<button formaction="{path}/publish">Опубликовать результат</button>')
        body+=panel("Проверка",form(request,path+"/draft",fields+actions,command=True))
    return page(request,"Проверка учебной работы",body,row["student_name"]+" · "+work.WORK_STATES[row["status"]])
