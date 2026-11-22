from datetime import timedelta
import json
from zoneinfo import ZoneInfo

from . import calendar_tools, personal_study, preparation, study_context, study_materials, study_timer, workspace
from .clock import now
from .domain import timestamp
from .schedule import local_value
from .ui import back, button, e, empty, form, hidden, input_field, link, local_label, page, panel, select_field, textarea

PLAN_STATE = {"draft": "Черновик", "confirmed": "Подтверждён", "cancelled": "Отменён"}
TIMER_STATE = {"running": "Идёт", "paused": "Пауза", "finished": "Завершён"}


def timer(request, db, notice="", picked=None):
    user = request.user
    rows = study_timer.sessions(db, user)
    todo = workspace.tasks(db, user, state="todo")
    picked = request.query("task") if picked is None else picked
    fields = select_field("task", "Связанная задача", [("", "Без задачи"), *((t["id"], t["title"]) for t in todo)], picked)
    body = panel("Учебная сессия", form(request, "/study/timer", fields + button("Начать"), command=True),
                 "Длительность определяется сохранёнными переходами. История обновляется при открытии.")
    if notice:
        body = f'<p role="alert">{e(notice)} {link("#current-timer", "К текущей сессии")}</p>' + body
    items = ""
    for row in rows:
        total = study_timer.seconds(row)
        minutes, seconds = divmod(total, 60)
        text = f'{TIMER_STATE[row["state"]]} · {minutes} мин {seconds} с'
        live = f' data-study-seconds="{total}"' if row["state"] == "running" else ""
        actions = ""
        if row["state"] != "finished":
            first = ("pause", "Пауза") if row["state"] == "running" else ("resume", "Продолжить")
            for action, caption in (first, ("finish", "Завершить")):
                path = f'/study/timer/{row["id"]}/{action}'
                actions += form(request, path, hidden("version", row["version"]) + hidden("task", picked or "") + button(caption), command=True)
        anchor = ' id="current-timer"' if row["state"] != "finished" else ""
        items += f'<li{anchor}><div><strong>{e(row["task_title"] or "Учебная сессия")}</strong><p{live}>{e(text)}</p>{actions}</div></li>'
    body += panel("История", f'<ul class="rows">{items}</ul>' if items else empty("Сессий пока нет."))
    return page(request, "Таймер", back("/study/preparation", "К подготовке") + body)


def index(request, db):
    user, selection = request.user, request.public
    zone = calendar_tools.period_for(user, selection).timezone
    today = now().astimezone(ZoneInfo(zone)).date()
    tasks = workspace.tasks(db, user, state="todo")
    exams = preparation.exams(db, user, selection)
    efforts = {task["id"]: preparation.effort(db, task["id"]) for task in tasks}
    numbers = [minutes for minutes in efforts.values() if minutes is not None]
    overdue = sum(1 for t in tasks if t["due_at"] and t["due_at"] < timestamp())
    block_count = db.execute("""
        SELECT count(*) FROM study_preparation_event p JOIN personal_event e ON e.id=p.event_id
        WHERE e.owner_id=? AND e.ends_at>?
    """, (user["id"], timestamp())).fetchone()[0]
    summary = (f'<p>Задач: {len(tasks)}. С оценкой длительности: {len(numbers)} ({sum(numbers)} мин). '
               f'Просрочены: {overdue}. Будущих блоков: {block_count}. Экзаменов в выбранном контексте: {len(exams)}.</p>')
    body = panel("Учебная нагрузка", summary, link("/study/timer", "Открыть таймер"))
    items = "".join(f'<li>{link("/tasks/" + task["id"],task["title"])} · '
                    f'{e(str(efforts[task["id"]]) + " мин" if efforts[task["id"]] else "уточни длительность")}</li>'
                    for task in tasks[:12])
    if items:
        body += panel("Задачи", f'<ul class="rows">{items}</ul>')
    fields = hidden("context_revision", study_context.preference(db, user)[1])
    task_option = [("", "Все мои незавершённые задачи"), *((task["id"], task["title"]) for task in tasks)]
    fields += select_field("task", "Задача", task_option, request.query("task"))
    fields += select_field("exam", "Срок из расписания", [("", "Свой срок"), *((key, title) for key,title,_ in exams)],
                           request.query("exam"))
    fields += input_field("deadline", "Свой крайний срок", (today+timedelta(days=7)).isoformat()+"T21:00", kind="datetime-local")
    fields += input_field("first", "Начальный день", today.isoformat(), "required", "date")
    fields += input_field("from", "Начало доступного времени", "10:00", "required", "time")
    fields += input_field("to", "Конец доступного времени", "20:00", "required", "time")
    fields += input_field("daily", "Минут в день", 120, 'min="1" max="720" required', "number")
    fields += input_field("block", "Минут за подход", 40, 'min="1" max="120" required', "number")
    fields += input_field("rest", "Минут перерыва", 10, 'min="0" max="90" required', "number")
    hint = "<p>До 31 дня. Раньше планируются задачи с ближайшим сроком. Используются известные интервалы текущего календаря.</p>"
    body += panel("Рассчитать план", hint + form(request, "/study/preparation", fields+button("Посмотреть черновик"), command=True))
    plans = preparation.state(db, user)
    items = "".join(f'<li>{link("/study/preparation/" + row["id"], PLAN_STATE[row["state"]])} · '
                    f'{e(local_label(row["deadline_at"], zone))}</li>' for row in plans)
    if items:
        body += panel("История планов", f'<ul class="rows">{items}</ul>')
    return page(request, "Подготовка", body, zone)


def detail(request, db, pid):
    user = request.user
    row = preparation.state(db, user, pid)
    selection = (row["source_id"], row["group_id"]) if row["source_id"] else None
    zone = calendar_tools.period_for(user, selection).timezone
    path = f"/study/preparation/{pid}"
    fields = hidden("version", row["version"]) + hidden("context_revision", study_context.preference(db, user)[1])
    chosen = json.loads(row["blocks_json"])
    is_draft = row["state"] == "draft"
    body = back("/study/preparation", "К подготовке")
    reason = preparation.review_reason(db, user, row, existing=row["state"]=="confirmed")
    if reason:
        body += f'<p role="status">{e(reason)} {link("/study/preparation", "Рассчитать заново")}</p>'
    items = ""
    for index, block in enumerate(chosen):
        times = local_label(block["start"], zone) + " — " + local_label(block["end"], zone)
        edit = ""
        if is_draft:
            edit_params = fields+hidden("index", index)
            edit_params += input_field("starts", "Начало блока", local_value(block["start"], zone).strftime("%Y-%m-%dT%H:%M"),
                                       "required", "datetime-local")
            edit = form(request, path+"/move", edit_params+button("Перенести в черновике"), command=True)
        items += f'<li><div><strong>{e(block["title"])}</strong><p>{e(times)}</p>{edit}</div></li>'
    body += panel("Блоки подготовки", f'<ul class="rows">{items}</ul>' if items else empty("Свободных блоков по этим правилам нет."))
    remaining = json.loads(row["remaining_json"])
    if remaining:
        items = "".join(f'<li>{e(item["title"])} · {e(item["reason"])}</li>' for item in remaining)
        body += panel("Что осталось вне плана", f'<ul class="rows">{items}</ul>')
    if is_draft:
        if chosen:
            body += form(request, path+"/confirm", fields+button("Подтвердить и добавить в календарь"), command=True)
        body += form(request, path+"/cancel", fields+button("Отменить черновик"), command=True)
    if row["state"] == "confirmed":
        items = "".join(f'<li>{link("/events/" + item["id"], item["title"])}</li>' for item in db.execute("""
            SELECT e.id,e.title FROM study_preparation_event p JOIN personal_event e ON e.id=p.event_id
            WHERE p.plan_id=? AND e.owner_id=? ORDER BY e.starts_at,e.id
        """, (pid,user["id"])))
        body += panel("Сохранённые события", f'<ul class="rows">{items}</ul>' if items else empty("События уже удалены."))
    return page(request, "План подготовки", body, PLAN_STATE[row["state"]] + " · " + local_label(row["deadline_at"], zone))


def materials(request, db, sid):
    subject = personal_study.subject(db, request.user, sid)
    base = f"/study/subjects/{sid}/materials"
    body = back(f"/study/subjects/{sid}", "К предмету")
    output = ""
    for row in study_materials.materials(db, request.user, sid):
        title = e(row["title"])
        if row["kind"] == "file":
            title = link(f'/study/materials/{row["id"]}/download', row["title"]) + f' · {row["size"]} байт'
        elif row["kind"] == "url":
            title = link(row["value"], row["title"])
        note = '<p class="multiline">' + e(row["value"]) + '</p>' if row["kind"] == "note" else ""
        remove = form(request, f'{base}/{row["id"]}/remove', button("Удалить материал"), command=True)
        output += f'<li><div>{title}{note}{remove}</div></li>'
    body += panel("Мои материалы", f'<ul class="rows">{output}</ul>' if output else empty("Материалов пока нет."))
    name = input_field("title", "Название", options='required maxlength="120"')
    fields = hidden("kind","note") + name + textarea("value", "Текст", 1800, required=True)
    body += panel("Заметка", form(request, base, fields+button("Сохранить заметку"), command=True))
    fields = hidden("kind","url")+name+input_field("value", "HTTPS-ссылка", options='required maxlength="320"',kind="url")
    body += panel("Ссылка", form(request, base, fields+button("Сохранить ссылку"), command=True))
    fields = '<label>Файл PNG или TXT<input type="file" name="file" accept=".png,.txt" required></label>'
    body += panel("Вложение до 2 МиБ", form(request, base, fields+button("Прикрепить"), command=True, multipart=True))
    return page(request, "Материалы", body, subject["title"])
