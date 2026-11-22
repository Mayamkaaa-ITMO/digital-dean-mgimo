from datetime import timedelta
import json
from urllib.parse import urlencode
from zoneinfo import ZoneInfo

from . import calendar_export as export, calendar_import as imports, calendar_tools, public_views, study_context
from .clock import now
from .domain import DomainError, role
from .schedule import local_value
from .ui import back, button, e, empty, form, hidden, input_field, link, page, panel, select_field, textarea

DRAFT_STATES={"draft":"Черновик","confirmed":"Подтверждён","cancelled":"Отменён"}


def import_index(request,db, *, csv_error="", csv_text="", csv_upload=False):
    role(request.user,"student")
    if request.method == "GET" and request.query("template") == "csv":
        return 200, imports.CSV_EXAMPLE, [
            ("Content-Type", "text/csv; charset=utf-8"),
            ("Content-Disposition", 'attachment; filename="dean-calendar-template.csv"'),
        ]
    body=back("/schedule","К расписанию")
    body += panel("Расписание по фото", "<p>Загрузи снимок таблицы, чтобы заполнить черновик.</p>"
                  + link("/calendar/import/photo", "Распознать расписание по фото", "ux-flow-link"))
    csv_path = "/calendar/import/csv"
    error = (f'<p id="calendar-csv-error" role="alert" tabindex="-1" data-restore-focus>{e(csv_error)}</p>'
             if csv_error else "")
    described = ' aria-invalid="true" aria-describedby="calendar-csv-error"' if csv_error else ""
    file_description = described if csv_upload else ' aria-describedby="csv-file-help"'
    file_input = (f'<label>Файл CSV<input type="file" name="file" required{file_description}></label>'
                  '<p class="meta" id="csv-file-help">Файл в UTF-8, до 64 КиБ и 100 записей.</p>')
    file_hint = '<p>Исправь файл и выбери его снова.</p>' if csv_upload else ""
    file_form = form(request, csv_path, (error if csv_upload else "") + file_hint + file_input
                     + button("Загрузить для проверки"), command=True, multipart=True)
    text_description = described if csv_error and not csv_upload else ' aria-describedby="csv-text-help"'
    text_input = (f'<label>Текст CSV<textarea name="data" maxlength="{imports.CSV_TEXT_LIMIT}" required data-csv-input'
                  f'{text_description}>\n{e(csv_text)}</textarea></label>'
                  '<p class="meta" id="csv-text-help">Вставь заголовок и строки CSV, до 6 000 символов.</p>')
    text_form = form(request, csv_path, (error if not csv_upload else "") + text_input
                     + '<button type="button" class="quiet" data-insert-csv hidden>Вставить пример</button>'
                     + button("Проверить текст"), command=True)
    rules = ("Между значениями ставь запятую. Время укажи в часовом поясе календаря. "
             "Для события на весь день введи две даты, "
             "где последняя дата в событие не входит. Сохраняй ключ при переносе. "
             "Для отмены ранее импортированной записи укажи cancelled.")
    body += ('<p>Загрузи файл или вставь текст. После проверки откроется черновик с номерами строк. '
             'События попадут в календарь после подтверждения.</p>'
             '<p><a class="ux-flow-link" href="/calendar/import?template=csv" download>Скачать шаблон CSV</a></p>'
             '<details class="ux-import-example"><summary>Посмотреть пример и формат CSV</summary>'
             f'<pre class="calendar-csv-example" data-csv-example>{e(imports.CSV_EXAMPLE)}</pre>'
             f'<p class="meta">{e(rules)}</p></details>')
    body += ('<div class="grid ux-import-methods">'
             + panel("Загрузить CSV", file_form)
             + panel("Вставить CSV", text_form) + "</div>")
    draft_path = "/calendar/import/draft"
    draft_file = '<label>Файл<input type="file" name="file" required></label>'
    body += ('<details class="panel"><summary>Другой формат · ScheduleDraft 1.0</summary>'
             + form(request, draft_path, draft_file + button("Загрузить для проверки"), command=True, multipart=True)
             + form(request, draft_path, textarea("data", "Текст", 6000, required=True)
                    + button("Проверить текст"), command=True) + "</details>")
    rows=db.execute("""
        SELECT id,state,created_at FROM calendar_import WHERE owner_id=? ORDER BY created_at DESC,id LIMIT 20
    """,(request.user["id"],)).fetchall()
    if rows:
        body+=panel("История импорта",'<ul class="rows">'+ "".join(
            f'<li>{link("/calendar/import/"+row["id"],DRAFT_STATES[row["state"]])} · {e(row["created_at"][:10])}</li>'
            for row in rows)+"</ul>")
    return page(request, "Импорт календаря", body, section="calendar-import")


def import_detail(request,db,did, *, problem=""):
    draft=imports.get(db,request.user,did)
    rows=json.loads(draft["rows_json"])
    selected = [row for row in rows if not row.get("excluded")]
    path=f"/calendar/import/{did}"
    body=back("/calendar/import","К импорту")
    week = imports.photo_week(db, request.user, did)
    photo_again = ""
    if week:
        from . import campus_photo
        if campus_photo.lab_mode():
            rec = db.execute("SELECT method FROM lab_photo_result WHERE draft_id=?", (did,)).fetchone()
            if rec: body += f'<p class="meta">{e(rec[0])}</p>'
        monday = study_context.date_value(week)
        sunday = monday + timedelta(days=6)
        caption = f"Неделя {monday:%d.%m.%Y} — {sunday:%d.%m.%Y}"
        zone = "МСК" if draft["zone"] == "Europe/Moscow" else draft["zone"]
        photo_again = "/calendar/import/photo?" + urlencode({"week": week})
        body += f'<p><strong>{caption}</strong> · {e(zone)}</p>'
        if draft["state"] == "draft":
            body += f'<p>{link(photo_again, "Загрузить снимок для другой недели")}</p>'
    if problem:
        body += f'<p class="change" role="alert" tabindex="-1" data-restore-focus>{e(problem)}</p>'
    if draft["state"]!="draft":
        confirmed = draft["state"] == "confirmed"
        description = ("Изменения применены к твоему календарю." if draft["state"] == "confirmed"
                       else "Загрузи новый снимок, чтобы начать заново." if week
                       else "Чтобы начать заново, вернись к загрузке CSV.")
        if week and draft["state"] == "confirmed" and request.query("repeat") == "1":
            description = "Этот снимок уже был импортирован на выбранную неделю. Повторный импорт не изменил календарь."
        count = f" · выбрано строк: {len(selected)} из {len(rows)}" if confirmed else ""
        result = f'<p><strong>{DRAFT_STATES[draft["state"]]}{count}.</strong></p><p>{description}</p>'
        if week and draft["state"] == "cancelled":
            result += link(photo_again, "Загрузить фото")
        else:
            day = study_context.date_value(week) if week else None
            if confirmed:
                zone = calendar_tools.period_for(request.user, request.public).timezone
                imported_dates = []
                for row in selected:
                    _, values = imports.normalized(row, draft["zone"])
                    if values:
                        imported_dates.append(local_value(values[2], zone).date())
                day = min(imported_dates, default=day)
            schedule = "/schedule"
            if day:
                schedule += "?" + calendar_tools.schedule_query(day, "day" if confirmed else "week")
            result += link(schedule, "Открыть в календаре" if week else "Открыть календарь")
        if week and draft["state"] == "confirmed" and campus_photo.lab_mode():
            result += " · " + link(path+"/calendar.ics","Скачать пары для Apple Calendar или другого календаря (.ics)")
        return page(request, "Импорт календаря", body + panel("Результат", result),
                    section="calendar-import")
    version=hidden("version",draft["version"])
    failures=imports.errors(rows,draft["zone"],db,request.user,draft["origin_key"],week)
    impacts, overlapping = imports.impacts(rows, draft["zone"], db, request.user, draft["origin_key"], failures,
                                          from_photo=bool(week))
    if failures:
        next_step = "Исправь или пропусти строки с ошибками, затем подтверди импорт."
    elif not selected:
        next_step = "Верни хотя бы одну строку, чтобы подтвердить импорт."
    else:
        next_step = "Сверь выбранные строки. После подтверждения календарь применит показанные изменения."
    body += ('<div class="ux-import-count">'
             f'<p><strong>К импорту {len(selected)} из {len(rows)}</strong> · Проверить {len(failures)}</p>'
             f'<p>{next_step}</p></div>')
    if not week:
        zone_form = form(request, path + "/zone", version + input_field("zone", "Идентификатор часового пояса",
                         draft["zone"], 'maxlength="80" required') + button("Сохранить зону"), command=True)
        zone_label = "МСК" if draft["zone"] == "Europe/Moscow" else draft["zone"]
        body += f'<details class="ux-import-example"><summary>Часовой пояс · {e(zone_label)}</summary>{zone_form}</details>'
    for row in rows:
        labels={"key":"Ключ события","subject":"Предмет","kind":"Тип в файле",
                "start":"Начало","end":"Конец","room":"Место"}
        fields=version+hidden("line",row["line"])
        if week:
            fields += hidden("key", row["key"])
            fields += input_field("subject", "Предмет", row["subject"], 'required maxlength="120"')
            fields += input_field("kind", "Тип занятия", row["kind"], 'maxlength="120"')
            fields += input_field("room", "Аудитория", row["room"], 'maxlength="120"')
            for name, label in (("start", "Начало"), ("end", "Окончание")):
                fields += input_field(name, label, row[name], 'required step="1"', "datetime-local")
        else:
            fields+="".join(input_field(name,label,row[name], 'maxlength="160"') for name,label in labels.items())
            fields+=select_field("status","Статус",[("active","Активное"),("cancelled","Отменено")],row["status"])
        notes=""
        photo_warning = any(note["field"] == "Фото" for note in row["warnings"])
        if row["warnings"]:
            if not row["reviewed"] and not row.get("excluded"):
                notes = ('<ul class="rows dean-import-warning">'
                         + "".join(f'<li>{e(note["message"])}</li>' for note in row["warnings"]) + "</ul>")
            fields+=f'<label class="check"><input type="checkbox" name="reviewed" value="1"{" checked" if row["reviewed"] else ""}>Сверил строку</label>'
        if row["event_basis"] or (week and row["basis"]):
            fields+=f'<label class="check"><input type="checkbox" name="replace" value="1"{" checked" if row["replace"] else ""}>Явно применить к ранее правленному личному событию</label>'
        excluded = bool(row.get("excluded"))
        if excluded:
            feedback = "Строка исключена из импорта."
        else:
            feedback = failures.get(row["line"], impacts.get(row["line"], "Строка готова к подтверждению."))
        if row["line"] in overlapping:
            notes += '<p>По времени пересекается с другой строкой этого файла.</p>'
        exclusion = version + hidden("line", row["line"]) + hidden("excluded", "0" if excluded else "1")
        toggle_label = "Вернуть строку" if excluded else "Исключить строку"
        toggle = form(request, path + "/exclude", exclusion + f'<button class="quiet">{toggle_label}</button>', command=True)
        edit = ""
        if not excluded:
            open_attr = " open" if row["line"] in failures and not photo_warning else ""
            edit = (f'<details{open_attr}><summary>Исправить строку</summary>'
                    + form(request, path + "/row", fields + button("Сохранить строку"), command=True) + "</details>")
        times = [row[field].replace("T", " ") for field in ("start", "end") if row[field]]
        if week and row["kind"]:
            times.append(row["kind"])
        values = " · ".join(times + ([row["room"]] if row["room"] else []))
        summary = (f'<p class="dean-import-subject"><strong>{e(row["subject"] or "Название нужно уточнить")}</strong></p>'
                   f'<p class="meta">{e(values)}</p>')
        tone = "attention" if row["line"] in failures else "neutral" if excluded else "good"
        actions = f'<div class="dean-photo-actions">{edit}{toggle}</div>'
        body += panel(f'Строка {row["line"]}', summary + f'<p class="tag" data-tone="{tone}">{e(feedback)}</p>'
                      + notes + actions, style="ux-import-row dean-photo-row")
    commands = ""
    if not failures and selected:
        commands += form(request, path + "/confirm", version + button("Подтвердить личный импорт"), command=True)
    elif failures:
        body += '<p class="meta">Подтверждение доступно после проверки выбранных строк.</p>'
    commands += form(request, path + "/cancel", version + '<button class="quiet">Отменить черновик</button>', command=True)
    body += f'<div class="dean-action-row toolbar">{commands}</div>'
    return page(request, "Предпросмотр импорта", body, "Календарь изменится после подтверждения.",
                section="calendar-preview")


def download(body):
    return 200,body,[("Content-Type","text/calendar; charset=utf-8"),
                     ("Content-Disposition",'attachment; filename="dean-calendar.ics"')]


def export_page(request,db):
    user=request.user
    role(user,"student")
    selection=request.public
    zone=calendar_tools.period_for(user,selection).timezone
    day=now().astimezone(ZoneInfo(zone)).date()
    first=request.query("first",day.isoformat())
    last=request.query("last",(day+timedelta(days=30)).isoformat())
    scope=request.query("scope","study")
    fields=input_field("first","Первый день",first,"required","date")
    fields+=input_field("last","Последний день",last,"required","date")
    fields+=select_field("scope","Что экспортировать",list(export.EXPORT_SCOPES.items()),scope)
    if request.query("download")=="1":
        first,last=export.period(first,last,scope)
        return download(export.export(db,user,selection,first,last,scope))
    contents=("В ICS входят названия, даты, длительность, признак целого дня, идентификаторы, версии и отмены. "
              "Учебная область использует исходные занятия. Личная область включает твои события. "
              "Еженедельные повторы до 26 экземпляров разворачиваются в события.")
    body=back("/schedule","К расписанию")+panel("Состав выгрузки",f"<p>{e(contents)}</p>",
                 public_views.scope_label(selection) if selection else "Учебное демо")
    body+=panel("Файл ICS",'<form method="get" action="/calendar/export">'+fields+
                hidden("download","1")+button("Скачать файл календаря (.ics)")+"</form>")
    revision=hidden("context_revision",study_context.preference(db,user)[1])
    body+=panel("Ссылка календаря","<p>Ссылку получает любой, кому ты её передашь. Доступ ограничен выбранными датами и областью.</p>"
                 +form(request,"/calendar/export/subscription",revision+fields+button("Создать ссылку для подписки"),command=True))
    items=""
    for row in export.feeds(db,user):
        label=f'{row["first_day"]} — {row["last_day"]} · {export.EXPORT_SCOPES[row["visibility"]]}'
        context=public_views.scope_label((row["source_id"],row["group_id"])) if row["source_id"] else "Учебное демо"
        action=(form(request,f'/calendar/export/subscription/{row["id"]}/revoke',button("Отозвать"))
                if not row["revoked_at"] else empty("Отозвана."))
        items+=f'<li><div>{e(label)}<p>{e(context)}</p>{action}</div></li>'
    body+=panel("Созданные подписки",f'<ul class="rows">{items}</ul>' if items else empty("Подписок пока нет."))
    return page(request,"Экспорт календаря",body)


def reveal(request,token):
    url=request.origin+"/calendar/feed/"+token
    body=panel("Календарная ссылка",input_field("url","Адрес для подписки",url,"readonly")
               + "<p>Ссылка показывается здесь после создания. В списке подписок можно отозвать доступ.</p>")
    return page(request,"Подписка создана",body+back("/calendar/export","К списку подписок"))

def photo_ics(request, db, did):
    from . import campus_lab, workspace
    user = request.user
    campus_lab.access(user)
    draft = imports.get(db,user,did)
    week = imports.photo_week(db,user,did)
    if not week or draft["state"] != "confirmed":
        raise DomainError(404,"Сначала подтверди импорт этого расписания.")
    events = {}
    for row in json.loads(draft["rows_json"]):
        if row.get("excluded") or row["status"]=="cancelled":
            continue
        key,data = imports.normalized(row,draft["zone"])
        imported = imports.current_event(db,user,draft["origin_key"],key)
        if imported:
            matches = db.execute("SELECT * FROM personal_event WHERE university_id=? AND owner_id=? AND id=?",
                                 (*workspace.scope(user),imported["event_id"])).fetchall() if not imported["cancelled"] else []
        else:
            title,_,start,end,_,_ = data
            possible = db.execute("SELECT * FROM personal_event WHERE university_id=? AND owner_id=? AND starts_at=? AND ends_at=?",
                                  (*workspace.scope(user),start,end))
            matches = [r for r in possible if " ".join(r["title"].casefold().split())==" ".join(title.casefold().split())][:1]
        for event in matches:
            uid,zone,version=export.personal_zone(db,user,event)
            events[uid]=export.Item(uid,event["title"],event["starts_at"],event["ends_at"],zone,bool(event["all_day"]),
                                    version,place=event["location"] or "")
    payload = export.serialize(sorted(events.values(),key=lambda r:(r.start,r.uid)), include_place=True)
    return 200,payload,[("Content-Type","text/calendar; charset=utf-8"),("Content-Disposition",f'attachment; filename="campus-{week}.ics"')]
