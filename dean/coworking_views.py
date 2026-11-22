from . import coworking as field
from .static_assets import asset_url
from .ui import back, button, e, form, input_field, link, local_label, page, panel, select_field

ZONE = "Europe/Moscow"


def asset():
    return f'<script defer src="{asset_url("field-camera.js")}"></script>'


def number(name,label,value,low,high):
    return input_field(name,label,str(value),f'required min="{low}" max="{high}" step="1"',"number")


def preview():
    return ('<div class="field-preview"><video muted playsinline></video><canvas aria-hidden="true"></canvas></div>'
            '<label>Камера<select data-devices><option value="">Системная камера</option></select></label>'
            '<button type="button" data-camera-start>Включить камеру</button> '
            '<button type="button" data-camera-stop disabled>Выключить</button>'
            '<p role="status" data-camera-feedback>Камера выключена.</p>')


def index(request,db):
    field.allowed(request)
    body = ''
    if request.user["role"]=="staff":
        request.allow_door_camera=True
        inputs = (input_field("room","Помещение","Коворкинг «Мастерская»",'required maxlength="80"')
                  +select_field("kind","Тип замера",list(field.KINDS.items()),"practice")
                  +number("capacity","Вместимость, мест","",1,10000)
                  +number("initial","Фактически людей внутри перед стартом","",0,10000)
                  +'<details open><summary>Настройка линии у двери</summary>'
                  +'<p>Поставь камеру сбоку от прохода. Включи предпросмотр и проверь, что человек виден по обе стороны линии.</p>'
                  + select_field("axis","Линия в кадре",[("x","Вертикальная"),("y","Горизонтальная")],"x")
                  + number("line","Положение линии, % от левого или верхнего края",50,20,80)
                  + number("gate_start","Начало дверного проёма вдоль линии, %",0,0,99)
                  + number("gate_end","Конец дверного проёма вдоль линии, %",100,1,100)
                  + select_field("inside","В какую сторону входят",[("1","Вправо / вниз"),("-1","Влево / вверх")],"1")
                  +preview()+'</details>'
                  +'<details><summary>Зона кампуса для проверки GPS</summary><p>Укажи центр и радиус. Координаты посетителя остаются на его устройстве.</p>'
                  +'<button type="button" data-mark-site>Взять центр из геолокации на месте</button><p role="status" data-location></p>'
                  +input_field("latitude","Широта центра","","inputmode=decimal")
                  +input_field("longitude","Долгота центра","","inputmode=decimal")
                  +number("radius","Радиус зоны, м",150,10,2000)+'</details>'
                  +button("Создать замер"))
        body += panel("Новый замер",'<div data-field-setup>'+form(request,field.PATH,inputs,command=True)+'</div>')
    sessions=db.execute("SELECT * FROM field_session WHERE university_id=? ORDER BY started DESC,rowid DESC LIMIT 100",
                        (request.user["university_id"],)).fetchall()
    items=''.join(f'<li>{link(field.PATH+"/"+r["id"],r["room"])} · {field.KINDS[r["kind"]]} · {e(local_label(r["started"],ZONE))} МСК · {"завершён" if r["ended"] else "открыт"}</li>' for r in sessions)
    body+=panel("Журнал замеров",f'<ul>{items}</ul>' if items else '<p>Пока нет замеров. Первый замер создаёт сотрудник.</p>')
    return page(request,"Загрузка коворкинга",asset()+body,"Анонимный подсчёт проходов у одной двери")


def detail(request,db,sid):
    field.allowed(request)
    row=field.session(db,request,sid)
    metrics=field.current(db,row)
    root=field.PATH+"/"+sid
    blocks=[("occupancy","Людей внутри"),("capacity","Мест"),("enter","Входов"),("exit","Выходов")]
    stats=''.join(f'<div><dt>{label}</dt><dd data-number="{k}">{metrics[k]}</dd></div>' for k,label in blocks)
    body=(asset()+back(field.PATH,"Все замеры")+f'<section data-field-session="{root}" data-axis="{row["axis"]}" '
          f'data-line="{row["line"]}" data-begin="{row["gate_start"]}" data-end="{row["gate_end"]}" data-inside="{row["inside"]}"><p>{field.KINDS[row["kind"]]} · '
          f'{e(local_label(row["started"],ZONE))} МСК · в начале {row["initial_count"]} чел.</p>'
          f'<dl class="field-stats">{stats}</dl><progress data-fill max="{row["capacity"]}" value="{row["occupancy"]}" '
          'aria-label="Доля занятых мест"></progress><p data-condition>'+e(metrics["condition"])+'</p><p data-last></p>'
          '<p data-uncertain '+('' if metrics["check"] else 'hidden')+'>Есть пропуск наблюдения. Сверь число людей внутри вручную.</p>')
    if row["campus_lat"] is not None:
        body+=panel("Зона кампуса",f'<div data-zone-lat="{row["campus_lat"]}" data-zone-lon="{row["campus_lon"]}" '
                    f'data-radius="{row["radius"]}"><button type="button" data-zone>Проверить, рядом ли я</button>'
                    '<p role="status" data-location></p></div>')
    if request.user["role"]=="staff" and not row["ended"]:
        request.allow_door_camera=True
        direction = {("x",1):"вправо",("x",-1):"влево",("y",1):"вниз",("y",-1):"вверх"}[(row["axis"],row["inside"])]
        controls=(f'<div data-field-live data-csrf="{e(request.csrf)}"><p>Линия на {row["line"]}%. Вход '
                  + direction
                  +'. Кадры обрабатываются в памяти на сервере деканата. Сохраняются только числовые показания и время.</p>'+preview()+'</div>')
        body+=panel("Камера у двери",controls)
        body+=panel("Ручная сверка",form(request,root+"/recount",number("count","Сколько людей сейчас внутри","",0,10000)+button("Сохранить пересчёт"),command=True,style="field-recount"))
    body += panel("Тепловая карта по минутам",'<p>Цвет показывает максимальную загрузку за минуту. В ячейке — минимум и максимум людей. Минуты появляются по мере поступления кадров.</p><ul class="field-heat" data-field-heat></ul>')
    if row["ended"]:
        witness=db.execute("SELECT entered,exited FROM field_reference WHERE session_id=?",(sid,)).fetchone()
        comparison = '<p>Ручной контроль проходов пока не внесён.</p>'
        if witness:
            comparison=('<table><thead><tr><th>Направление</th><th>Наблюдатель</th><th>Камера</th><th>Разница</th></tr></thead><tbody>'
                        +''.join(f'<tr><th>{label}</th><td>{human}</td><td>{auto}</td><td>{auto-human:+d}</td></tr>'
                                 for label,human,auto in [("Вход",witness["entered"],metrics["enter"]),("Выход",witness["exited"],metrics["exit"])])
                        +'</tbody></table>')
        if request.user["role"]=="staff":
            comparison+=form(request,root+"/reference",number("entered","Входы по независимому подсчёту",witness[0] if witness else "",0,99999)
                            +number("exited","Выходы по независимому подсчёту",witness[1] if witness else "",0,99999)
                            +button("Сохранить контрольные значения"),command=True)
        body+=panel("Сверка с наблюдателем",comparison)
    journal=''.join(f'<li>{e(local_label(j["at"],ZONE))} · {e(j["kind"])} · внутри {j["occupancy"]}</li>' for j in metrics["journal"])
    body+=panel("Последние события",f'<ol data-journal>{journal}</ol>'+link(root+"/export.csv","Скачать полный журнал CSV"))
    if request.user["role"]=="staff" and not row["ended"]:
        body+=form(request,root+"/end",button("Завершить замер"),command=True,style="field-end")
    return page(request,row["room"],body+'</section>')
