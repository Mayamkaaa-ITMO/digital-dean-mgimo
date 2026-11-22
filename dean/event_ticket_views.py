from base64 import b64encode

from . import event_tickets, reservations
from .domain import timestamp
from .ui import back, button, e, empty, form, hidden, input_field, link, page, panel


def menu(request,db,row):
    active=event_tickets.current(db,row["id"])
    seen=db.execute("SELECT 1 FROM reservation_attendance WHERE reservation_id=?",(row["id"],)).fetchone()
    if row["status"]!="reserved" or row["ends_at"]<=timestamp() or seen:
        return ""
    fields=hidden("ticket_id",active["id"] if active else "")
    where=f'/bookings/{row["id"]}/ticket'
    info="<p>Перевыпуск отменит предыдущий код.</p>" if active else "<p>Билет на демонстрационное мероприятие.</p>"
    return panel("Билет",info+form(request,where,fields+button("Перевыпустить" if active else "Получить билет"),command=True))


def issued(request,db,rid,code):
    row=reservations.get_booking(db,request.user,rid)
    image=b64encode(event_tickets.png(code)).decode("ascii")
    text=f'<p>{e(row["title"])}</p><img src="data:image/png;base64,{image}" width="225" height="225" alt="QR-код билета">'
    text+=input_field("ticket","Код для ручного ввода",code,"readonly")
    text+="<p>Сохрани код или покажи QR организатору. При перевыпуске этот билет перестанет действовать.</p>"
    return page(request,"Демонстрационный билет",back("/bookings/"+rid,"К регистрации")+panel("Предъявить организатору",text))


def check(request,db,sid):
    window=event_tickets.organizer(db,request.user,sid)
    path=f"/staff/slots/{sid}/tickets"
    fields=input_field("code","Код билета",options='required maxlength="50" autocomplete="off"')
    body=back("/staff/slots/"+sid,"К списку участников")
    body+=panel("Проверка билета",form(request,path,fields+button("Проверить код"))+
                "<p>Проверка открывает предпросмотр. Отметку сохраняет отдельное подтверждение.</p>")
    return page(request,"Проверка билетов",body,window["title"])


def review(request,db,sid,rid):
    row,staged,window=event_tickets.reviewed(db,request.user,sid,rid)
    path=f"/staff/slots/{sid}/tickets"
    person=e(row["full_name"])
    content=f'<p>Участник: {person}.</p>'
    if row["used_at"] or staged["applied_at"]:
        content+=empty("Посещение уже отмечено.")
    else:
        content+=form(request,path+"/"+rid,button("Подтвердить посещение"),command=True)
    return page(request,"Результат проверки",back(path,"Проверить другой билет")+panel("Действующая регистрация",content),window["title"])
