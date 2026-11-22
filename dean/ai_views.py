import json

from zoneinfo import ZoneInfo

from . import appearance, assistant as campus_assistant, calendar_tools, chat_history, photo_import
from .clock import now
from .domain import role, timestamp
from .schedule import local_value
from .static_assets import asset_url
from .ui import back, button, e, flow_note, form, hidden, input_field, link, page, panel, select_field


def photo(request, db, message="", week="", *, retry_id="", error_status=0, region="whole"):
    role(request.user, "student")
    zone = calendar_tools.period_for(request.user, request.public).timezone
    first = now().astimezone(ZoneInfo(zone)).date()
    default_week = photo_import.monday(first.isoformat()).isoformat()
    if photo_import.extended() and not week and (suggested := photo_import.image_date(request.query("week"))):
        week = suggested.isoformat()
    body = back("/calendar/import", "К импорту календаря")
    from . import campus_photo
    model_available = getattr(request.ai, "vision_enabled", request.ai.enabled)
    local_available = campus_photo.lab_mode()
    model = getattr(request.ai, "vision_label", "подключённую модель")
    if not model_available and not local_available:
        alternative = link("/calendar/import", "Импортировать CSV", "dean-action-link")
        body += flow_note("Распознавание фото пока недоступно",
                          "Можно загрузить таблицу CSV и проверить пары перед добавлением.",
                          alternative, tone="attention")
        return page(request, "Расписание по фото", body, section="calendar-photo")
    error = ""
    if message:
        alternative = (link("/calendar/import", "К импорту CSV", "dean-action-link")
                       if error_status in {429, 502, 503} else "")
        retry = ("Выбери тот же снимок, дату и часть, чтобы получить результат текущей попытки." if retry_id
                 else "Выбери снимок и попробуй ещё раз.")
        error = flow_note(message, retry, alternative,
                          tone="attention" if error_status == 429 else "error", error=True)
    processing = (f'Для распознавания снимок получит <strong>{e(model)}</strong>.'
                  if model_available else "Снимок обрабатывается на сервере кабинета.")
    privacy = f'<p class="meta">{processing} До подтверждения пары остаются в черновике.</p>'
    fields = ((hidden("request_id", retry_id) if retry_id else "")
              + input_field("week", "Дата в нужной неделе", week or default_week,
                         'required aria-describedby="photo-week-help"', "date")
              + '<p class="meta" id="photo-week-help">Подойдёт любой день нужной недели.</p>'
              + select_field("photo_region", "Какую часть снимка распознать", photo_import.REGIONS.items(), region)
              + '<p class="meta">Если нечётная и чётная недели стоят рядом, выбери сторону с нужным заголовком '
                'и дату этой недели.</p>'
              + '<label>Фото или скриншот<input type="file" name="file" accept="image/jpeg,image/png" '
                'required aria-describedby="photo-help"></label>'
              + '<p class="meta" id="photo-help">JPEG или PNG · до 20 МБ при подготовке в браузере</p>'
              + '<p class="dean-note" role="status" data-photo-status hidden></p>'
              + privacy + button("Распознать расписание по фото"))
    fields = form(request, "/calendar/import/photo", fields, command=not retry_id, multipart=True,
                  style="dean-photo-form dean-flow-form")
    primary = panel("Загрузи расписание", error + fields)
    aside = panel("Как сделать снимок",
                  "<p>В кадре должны быть дни, время и аудитории. Если две недели расположены одна под другой, "
                  "обрежь фото до нужной недели.</p>"
                  "<p>Проверь распознанные пары и исправь поля в черновике перед добавлением.</p>"
                  "<p>Сверь номера аудиторий и похожие кириллические и латинские буквы.</p>"
                  '<p class="meta">Браузер подготовит снимок до 2048 px по длинной стороне. '
                  'Если подготовка недоступна, выбери JPEG или PNG до 2 МБ. HEIC пока не поддерживается.</p>',
                  style="dean-flow-aside")
    return page(request, "Расписание по фото", body + f'<div class="dean-flow-split">{primary}{aside}</div>',
                "Из снимка одной недели получится черновик с парами.", section="calendar-photo")


def mascot(size=36):
    return (f'<img class="dean-mascot" src="{asset_url("assistant-mascot.svg")}" '
            f'width="{size}" height="{size}" alt="" decoding="async">')


def launcher(*, with_map=False):
    arrow = f'<span class="dean-shortcut-arrow">{appearance.icon("diagonal")}</span>'
    assistant = (
        '<a href="/assistant" class="dean-shortcut-tile dean-assistant-launcher" data-tour-assistant>'
        f'{mascot()}{arrow}<span><strong>ИИ-помощник</strong><small>Открыть чат</small></span></a>'
    )
    if not with_map:
        return f'<div class="dean-dashboard-shortcuts">{assistant}</div>'
    map_tile = (f'<a href="/campus-map" class="dean-shortcut-tile dean-map-launcher">'
                f'<span class="dean-map-tile-icon">{appearance.icon("map")}</span>{arrow}'
                '<span><strong>Карта кампуса</strong><small>Найти аудиторию</small></span></a>')
    return f'<div class="dean-dashboard-shortcuts dean-dashboard-shortcuts-paired">{map_tile}{assistant}</div>'


def conversation_url(conversation_id):
    return "/assistant?chat=" + conversation_id


def transcript(request, row):
    reply = json.loads(row["reply_json"])
    local = local_value(row["created_at"], request.user["timezone"])
    time = f'<time class="dean-chat-time" datetime="{e(row["created_at"])}">{local:%H:%M}</time>'
    user = ('<div class="dean-chat-message" data-chat-speaker="user">'
            f'<span class="dean-visually-hidden">Ты</span><p>{e(row["question"])}</p>{time}</div>')
    if reply.get("proposal"):
        pid = reply["proposal"]
        title = e(reply.get("caption") or reply["text"])
        if row["result_path"]:
            note = "Действие выполнено"
            action = link(row["result_path"], reply.get("result_label", "Открыть результат"), "dean-chat-link")
        elif not row["expires_at"] or row["expires_at"] <= timestamp():
            note = "Время подтверждения истекло"
            action = link(f"/assistant/actions/{pid}/confirm", "Проверить действие", "dean-chat-link")
        else:
            note = "Нужно подтверждение"
            action = form(request, "/assistant/actions/" + pid + "/confirm", button(reply.get("confirm_label", "Подтвердить")))
        body = (f'<div class="dean-chat-confirmation"><p class="dean-chat-card-label">{note}</p>'
                f'<p><strong>{title}</strong></p>{action}</div>')
    else:
        path = reply.get("link") or reply.get("navigate")
        caption = next((label for url, label, _ in campus_assistant.pages(request.user).values()
                        if url == (path or "").partition("?")[0]), "")
        follow = link(path, "Открыть " + caption.lower() if caption else "Открыть в кабинете",
                      "dean-chat-link") if path else ""
        if reply.get("section_links"):
            follow = " ".join(link(url, label, "dean-chat-link") for url, label in reply["section_links"])
        body = f'<p class="assistant-reply-text">{e(reply.get("text", ""))}</p>{follow}'
    return (f'<div class="dean-chat-turn" data-chat-command="{e(row["command_id"])}">{user}'
            f'<div class="dean-chat-answer">{mascot(26)}'
            f'<div class="dean-chat-message" data-chat-speaker="assistant">'
            f'<span class="dean-visually-hidden">Помощник</span>{body}{time}</div></div></div>')


def log_content(request, rows):
    today = now().astimezone(ZoneInfo(request.user["timezone"])).date()
    last = None
    parts = []
    for row in rows:
        day = local_value(row["created_at"], request.user["timezone"]).date()
        if day != last:
            label = "Сегодня" if day == today else day.strftime("%d.%m.%Y")
            parts.append(f'<p class="dean-chat-day" data-chat-day="{day:%Y-%m-%d}">{label}</p>')
            last = day
        parts.append(transcript(request, row))
    return "".join(parts)


def chat_header(request, db, conversation, embedded):
    items = []
    for thread in chat_history.conversations(db, request.user):
        current = ' aria-current="page"' if conversation and thread["id"] == conversation["id"] else ""
        items.append(f'<li><a href="{conversation_url(thread["id"])}" data-chat-thread="{e(thread["id"])}"{current}>'
                     f'{e(thread["title"])}</a></li>')
    recent = ('<p class="meta" data-chat-no-history>Здесь появятся твои диалоги.</p>' if not items else "")
    history = ('<details class="dean-chat-history" data-chat-history><summary>Чаты</summary>'
               f'<div class="dean-chat-history-popover">{recent}<ul data-chat-thread-list>{"".join(items)}</ul>'
               '<p><a href="/privacy">Политика обработки данных</a></p></div></details>')
    home = "/staff" if request.user["role"] == "staff" else "/"
    collapse = (f'<button type="button" class="dean-chat-collapse" data-chat-collapse hidden '
                f'aria-label="Свернуть чат" title="Свернуть чат">{appearance.icon("minimize")}</button>')
    back_action = "" if embedded else (
        f'<a href="{home}" class="dean-chat-back" data-chat-collapse aria-label="В кабинет" '
        f'title="В кабинет">{appearance.icon("left")}</a>')
    expand = (f'<a href="{conversation_url(conversation["id"] if conversation else "new")}" '
              f'class="dean-chat-expand" data-chat-expand aria-label="На весь экран" title="На весь экран">'
              f'{appearance.icon("expand")}</a>{collapse}') if embedded else ""
    return (f'<header class="dean-chat-header" data-tour-assistant>{back_action}{mascot()}'
            f'<div class="dean-chat-heading"><h2>Вишка</h2><p class="meta">ИИ-помощник деканата</p></div>'
            f'<div class="dean-chat-tools">{expand}{history}'
            '<a href="/assistant?chat=new" class="dean-chat-new" aria-label="Новый чат" title="Новый чат">＋</a></div></header>')


def chat(request, db, *, embedded=False, conversation=None, question="", error="", retry_id="", previous=""):
    from . import assistant_voice

    if embedded:
        conversation = chat_history.current(db, request.user)
    rows = chat_history.messages(db, request.user, conversation)
    selected = conversation["id"] if conversation else "new"
    choices = ("Какие пары завтра?", "Создай задачу подготовиться к семинару", "Забронируй коворкинг на завтра") if request.user["role"] == "student" else (
        "Расписание на неделю", "Очередь заявок")
    key = "dashboard-assistant" if embedded else "assistant-chat"
    if request.ai.enabled and request.user["role"] == "student":
        choices += ("Как подобрать дисциплины на четыре кредита?",)
    if request.ai.enabled:
        choices += ("Объясни производную на примере",)
    suggestions = "".join(f'<button type="button" class="quiet" data-chat-question="{e(choice)}" hidden>{e(choice)}</button>'
                          for choice in choices)
    guidance = ("Спроси про занятия и заявки, создай задачу или забронируй помещение."
                if request.user["role"] == "student" else "Спроси про расписание своих групп или очередь заявок.")
    if request.ai.enabled:
        guidance += " Можешь попросить объяснить учебную тему."
    if request.ai.enabled and request.user["role"] == "student":
        guidance += " Когда просишь разобрать материал из кабинета, помощник передаёт модели подходящие фрагменты текста."
    empty = (f'<div class="dean-chat-welcome" data-chat-welcome>{mascot(52)}<h3>Чем помочь?</h3>'
             f'<p>{guidance}</p>'
             f'<div class="dean-chat-suggestions" aria-label="Быстрые запросы">{suggestions}</div></div>')
    history = log_content(request, rows)
    log = f'<div class="dean-chat-log" data-chat-log role="log" aria-label="Сообщения" tabindex="0">{history or empty}</div>'
    feedback = ' data-state="error"' if error else " hidden"
    status = (f'<p class="dean-chat-status" role="status" data-chat-status{feedback}>{e(error)}</p>')
    dictation = ""
    if assistant_voice.local_model():
        dictation = (f'<button type="button" class="quiet dean-dictation" data-dean-dictation="{assistant_voice.PATH}" '
                     'aria-label="Голосовой ввод" aria-pressed="false" hidden>'
                     f'<span data-record-icon>{appearance.icon("mic")}</span>'
                     f'<span data-stop-icon hidden>{appearance.icon("stop")}</span></button>')
        status += '<p class="dean-chat-status dean-dictation-status" role="status" data-dictation-status hidden></p>'
    fields = (hidden("chat", selected) + hidden("previous", rows[-1]["question"] if rows else previous)
              + (hidden("request_id", retry_id) if retry_id else "")
              + '<label class="dean-chat-input"><span class="dean-visually-hidden">Сообщение</span>'
                '<textarea name="message" maxlength="700" rows="1" required placeholder="Сообщение">'
              + e(question) + '</textarea></label>'
              + dictation
              + f'<button type="submit" aria-label="{ "Повторить отправку" if error else "Отправить" }">'
                f'{appearance.icon("send")}<span class="dean-chat-retry" aria-hidden="true">Повторить</span></button>')
    composer = form(request, "/assistant", fields, command=not retry_id, style="dean-assistant-form")
    shell = "aside" if embedded else "section"
    mode = "embedded" if embedded else "page"
    return (f'<{shell} class="dean-chat dean-chat-{mode}" id="{key}" data-dean-chat '
            f'data-chat-owner="{e(request.user["id"])}">'
            f'{chat_header(request, db, conversation, embedded)}{log}'
            f'<div class="dean-chat-composer">{status}{composer}</div></{shell}>')


def assistant(request, db, *, question="", error="", retry_id="", selected=None, previous=""):
    choice = request.query("chat") if selected is None else selected
    conversation = chat_history.current(db, request.user, choice)
    body = chat(request, db, conversation=conversation, question=question, error=error,
                retry_id=retry_id, previous=previous)
    return page(request, "ИИ-помощник", body, section="assistant")


def review_action(request, db, pid, *, error=""):
    action, result = campus_assistant.proposal(db, request.user, pid)
    thread = db.execute(
        "SELECT conversation_id FROM assistant_message WHERE owner_id=? "
        "AND json_extract(reply_json, '$.proposal')=? ORDER BY rowid DESC LIMIT 1",
        (request.user["id"], pid),
    ).fetchone()
    back_link = back(conversation_url(thread["conversation_id"]) if thread else "/assistant", "К диалогу")
    caption = f'<p><strong>{e(action["caption"])}</strong></p>'
    if result:
        body = panel("Действие выполнено", caption + link(result, "Открыть результат", "rw-primary-link"))
    else:
        warning = flow_note("Не удалось подтвердить", error, tone="danger", error=True) if error else ""
        if error and action["action"] == "finish_task":
            task_id = json.loads(action["fields_json"])["id"]
            warning += link(f"/tasks/{task_id}", "Открыть задачу", "dean-chat-link")
        message = "<p>Выполнение ещё не подтверждено. Сверь детали и подтверди тот же запрос.</p>"
        confirm = form(request, f"/assistant/actions/{pid}/confirm", hidden("reviewed", "yes") + button("Подтвердить"))
        body = panel("Проверь действие", caption + warning + message + confirm)
    return page(request, "Действие Вишки", back_link + body)
