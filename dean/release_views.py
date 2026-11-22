from . import dashboard_settings, document_share
from .domain import role, timestamp
from .ui import back, button, e, empty, form, hidden, input_field, link, local_label, page, panel, select_field


def dashboard(request,db):
    user=request.user
    role(user,"student")
    mode=dashboard_settings.checked_mode(request.query("mode","public" if request.public else "demo"))
    item_order,shortcuts,version=dashboard_settings.preferences(db,user,mode)
    panels = ""
    places = [("0", "Скрыто"), *((str(index), str(index)) for index in range(1, len(dashboard_settings.PANELS[mode]) + 1))]
    for key,label in dashboard_settings.PANELS[mode].items():
        rank=item_order.index(key)+1 if key in item_order else 0
        panels += select_field("panel_" + key, label, places, str(rank))
    actions_grid = ""
    places = [("0", "Скрыто"), *((str(index), str(index)) for index in range(1, len(dashboard_settings.SHORTCUTS) + 1))]
    for key,(_,label) in dashboard_settings.SHORTCUTS.items():
        rank=shortcuts.index(key)+1 if key in shortcuts else 0
        actions_grid += select_field("shortcut_" + key, label, places, str(rank))
    fields = hidden("mode", mode) + hidden("version", version)
    fields += (f'<fieldset class="dean-dashboard-settings"><legend>Приоритеты</legend>{panels}</fieldset>'
               f'<fieldset class="dean-dashboard-settings"><legend>Быстрые действия</legend>{actions_grid}</fieldset>')
    variants = (("demo", "Демо-группа"), ("public", "Публичная группа"))
    links = "".join(f'<a href="/profile/dashboard?mode={key}"{" aria-current=page" if key == mode else ""}>{label}</a>'
                    for key, label in variants)
    body = (back("/", "На главную") + f'<nav class="switcher" aria-label="Главная для группы">{links}</nav>'
            '<p>Выбери позицию каждого блока или «Скрыто». Позиция 1 находится вверху.</p>')
    actions=button("Сохранить")+'<button class="quiet" name="reset" value="1">Сбросить настройки</button>'
    body+=form(request,"/profile/dashboard",fields+actions)
    return page(request, "Что показывать на главной", body)


def share(request,db,did):
    document=document_share.own_document(db,request.user,did)
    path=f"/documents/{did}/share"
    fields=select_field("duration","Срок",[(key,"На "+key+" ч") for key in document_share.DURATIONS],"24")
    fields+='<label class="check"><input type="checkbox" name="confirm" value="1">Передать доступ к этому демонстрационному PDF</label>'
    consent="<p>Обладатель ссылки сможет скачать этот PDF до истечения срока или отзыва.</p>"
    body=back("/documents","К документам")+panel("Временная ссылка",consent+form(request,path,fields+button("Создать"),command=True))
    rows=""
    for grant in document_share.links(db,request.user,did):
        status="Отозвана" if grant["revoked_at"] else "Истекла" if grant["expires_at"]<=timestamp() else "Доступна"
        control=form(request,path+"/"+grant["id"]+"/revoke",button("Отозвать")) if status=="Доступна" else ""
        rows+=f'<li><div>{status} · {e(local_label(grant["expires_at"],request.user["timezone"]))}{control}</div></li>'
    body+=panel("Выданные ссылки",f'<ul class="rows">{rows}</ul>' if rows else empty("Ссылок пока нет."))
    return page(request,"Поделиться документом",body,document["title"])


def reveal_document(request,did,secret):
    url=request.origin+"/shared/document/"+secret
    body=input_field("url","Временный адрес",url,"readonly")
    body+="<p>Адрес показан один раз. Отзыв остановит следующие скачивания.</p>"
    return page(request,"Ссылка на документ",panel("Адрес",body)+back(f"/documents/{did}/share","Управлять ссылками"))
