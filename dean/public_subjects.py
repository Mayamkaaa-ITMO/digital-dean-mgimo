"""References inside a confirmed public group and semester."""
import hashlib

from . import personal_study, public_schedule, public_views
from .domain import require, role
from .ui import back, button, e, empty, form, hidden, link, local_label, page, panel


def subject_key(selection, title):
    basis = public_schedule.dump([*selection, personal_study.period(selection), title]).encode()
    return hashlib.sha256(basis).hexdigest()[:32]


def subject_href(selection, title):
    return "/study/public/subjects/" + subject_key(selection, title)


def subjects(request, db):
    role(request.user, "student")
    selection=request.public
    if not selection:
        return page(request,"Дисциплины расписания",link("/schedule/context","Выбрать публичную группу"))
    names=personal_study.suggestions(db,selection)
    items="".join(f'<li>{link(subject_href(selection,name),name)}</li>' for name in names)
    body=panel("Названия из расписания",
               f'<ul class="rows">{items}</ul>' if items else empty("В опубликованном охвате предметов пока нет."),
               link("/schedule","К расписанию"))
    return page(request,"Дисциплины расписания",body,public_views.scope_label(selection))


def detail(request,db,key):
    role(request.user,"student")
    selection=request.public
    title=require(next((title for title in personal_study.suggestions(db,selection)
                        if subject_key(selection,title)==key),None))
    zone=public_schedule.scope(selection).timezone
    rows=db.execute("""
        SELECT e.id,e.kind,e.starts_at,e.ends_at,e.monday,w.observed_at,w.published_at,w.provenance
        FROM public_lesson e JOIN public_week w USING(source_id,group_id,monday)
        WHERE e.source_id=? AND e.group_id=? AND e.subject=? AND e.cancelled=0 ORDER BY e.starts_at,e.id
    """,(*selection,title)).fetchall()
    items=[]
    for row in rows:
        week=public_schedule.week_scope(*selection,row["monday"])
        when=local_label(row["starts_at"],zone)+" · "+row["kind"]
        label=f'{public_views.MODE[row["provenance"]]} · получено {row["observed_at"]}'
        items.append(f'<li><div>{link("/schedule/public/"+row["id"],when)}<p class="meta">{e(label)}</p>'
                     f'{link(week.url,"Официальная неделя")}</div></li>')
    body=back("/study/public/subjects","К дисциплинам")
    if items:
        body+=panel("Опубликованные занятия",'<ul class="rows">'+"".join(items)+"</ul>")
    group=public_schedule.SOURCES[selection[0]]["groups"][selection[1]]
    pattern=[record for record in group.get("pattern",[]) if record["subject"]==title]
    if pattern:
        items=[]
        for row in pattern:
            where=link(public_views.room_href("ref:"+row["room_ref"]),row.get("room","Помещение")) if row.get("room_ref") else e(row.get("room",""))
            items.append(f'<li>{e(row["day"])} · {e(row["start"])}–{e(row["end"])} · {where}</li>')
        body+=panel("Справочная сетка без календарных дат",'<ul class="rows">'+"".join(items)+"</ul>")
    assoc = link("/study/public/services/programs","Опубликованный каталог программ") if any(
        service["id"]=="programs" for service in public_schedule.SOURCES[selection[0]]["services"]) else ""
    info=f'<p>{e(personal_study.period(selection))} · {e(zone)}</p>'
    info+="<p>Название подтверждено расписанием группы. Связь с учебной программой, кредиты и содержание требуют отдельного подтверждения.</p>"
    info+=assoc
    body+=panel("Учебный контекст",info)
    body+=panel("Личный предмет",form(request,"/study/subjects",hidden("source_title",title)
                                      +button("Сохранить в моих предметах"),command=True))
    return page(request,title,body,public_views.scope_label(selection))


def service(request,db,key):
    role(request.user,"student")
    selection=request.public
    origin=public_schedule.SOURCES.get(selection[0],{}) if selection else {}
    found=require(next((entry for entry in origin.get("services",()) if entry["id"]==key),None))
    body=back("/student-services","К справочнику")
    facts=[]
    for label,name in (("Адрес","address"),("Сведения","detail")):
        if found.get(name):
            facts.append(f'<p>{e(label)} · {e(found[name])}</p>')
    if not facts:
        facts.append(empty("Справочная запись ведёт к первоисточнику. Содержание и порядок обращения проверяй на сайте вуза."))
    body+=panel("Сведения источника","".join(facts)+link(found["url"],"Официальный сайт"))
    bells=origin.get("bell_schedule")
    if key=="calendar" and bells:
        rows="".join(f'<li>{row["number"]} · {e(row["start"])}–{e(row["end"])}</li>' for row in bells["slots"])
        body+=panel("Опубликованная сетка звонков",f'<p>Время кампуса · {e(origin["zone"])}. '
                    f'Справка прочитана {e(bells["read_date"])}.</p><ul class="rows">{rows}</ul>',
                    link(bells["url"],"Источник сетки"))
    return page(request,found["label"],body,public_views.scope_label(selection))
