import json

from . import telegram
from .domain import timestamp
from .static_assets import asset_url
from .telegram_transport import TelegramConfig
from .ui import back, button, e, flow_note, form, hidden, input_field, link, page, panel

ROOT = "/profile/telegram"


def chat_preview(staff):
    role = "staff" if staff else "student"
    choices = "".join(f'<span>{e(label)}</span>' for row in telegram.MENUS[role][:2] for label in row)
    description = ("Покажу расписание и статусы заявок твоих групп."
                   if staff else "Покажу пары, задачи и статусы заявок. Могу присылать напоминания.")
    return panel("Как выглядит чат",
                 '<div class="dean-telegram-preview">'
                 f'<img src="{asset_url("telegram-avatar.jpg")}" alt="" width="32" height="32">'
                 f'<div><strong>Привет, я Вишка!</strong><p>{description}</p></div>'
                 f'<div class="dean-telegram-preview-menu">{choices}</div></div>', style="dean-flow-aside")


def checkbox(name, label, checked=False):
    return f'<label class="check"><input type="checkbox" name="{name}" value="1"{" checked" if checked else ""}>{e(label)}</label>'


def settings_form(request, attached):
    chosen = set(json.loads(attached["categories_json"]))
    categories = "".join(checkbox(key, label, key in chosen) for key, label in telegram.CATEGORIES.items())
    quiet = (input_field("quiet_start", "Начало тихих часов", attached["quiet_start"], "required", "time")
             + input_field("quiet_end", "Конец тихих часов", attached["quiet_end"], "required", "time"))
    fields = (hidden("version", attached["version"])
              + checkbox("enabled", "Разрешить уведомления", attached["enabled"])
              + f'<fieldset><legend>Категории уведомлений</legend>{categories}</fieldset>'
              + '<fieldset><legend>Тихие часы</legend>'
              + input_field("timezone", "Часовой пояс", attached["timezone"], 'required maxlength="80"')
              + f'<div class="dean-telegram-time">{quiet}</div></fieldset>'
              + checkbox("summary", "Сводка дня после 09:00", attached["summary"])
              + button("Сохранить настройки"))
    return form(request, ROOT + "/settings", fields, style="dean-telegram-settings dean-flow-form")


def linked(request, config, attached, staff):
    current = config.name == attached["bot"]
    state = "Привязано" if current else "Привязка к предыдущей конфигурации"
    note = flow_note(state, "Бот подключён к кабинету." if current else "Для нового бота сначала отключи текущую привязку.",
                     tone="positive" if current else "attention")
    options = ("<p>В личном чате доступны расписание и очередь твоих групп.</p>" if staff else
               settings_form(request, attached))
    primary = panel("Подключение" if staff else "Уведомления", note + options, style="dean-telegram-account")
    open_bot = link("https://t.me/" + config.name, "Открыть бота", "dean-action-link") if current else ""
    revoke = form(request, ROOT + "/revoke", button("Отключить"), style="dean-telegram-revoke")
    confirmation = ('<details><summary>Отключить Telegram</summary><p><strong>Отключить Telegram?</strong></p>'
                    '<p>Бот перестанет присылать уведомления. Канал можно подключить снова.</p>'
                    f'<div class="dean-action-row">{revoke}'
                    + link(ROOT, "Оставить подключённым", "dean-action-link") + "</div></details>")
    side = panel("Подключённый чат", open_bot + confirmation, style="dean-flow-aside dean-telegram-manage")
    return f'<div class="dean-flow-split">{primary}{side}</div>'


def pairing(request, pending, staff):
    begin = form(request, ROOT + "/begin", button("Получить новую ссылку" if pending else "Подключить Telegram"))
    if pending and pending["attempts"] < 6:
        fields = input_field("proof", "Код из сообщения", options='required maxlength="12" autocomplete="off" spellcheck="false"')
        waiting = ("Если сообщения ещё нет, нажми «Запустить» в личном чате." if not pending["chat_id"] else
                   "Код действует до конца десятиминутной привязки.")
        content = (f'<p class="meta">Шаг 2 из 2</p><p>Введи 12 символов из сообщения бота. {waiting}</p>'
                   + form(request, ROOT + "/confirm", fields + button("Подтвердить"), style="dean-telegram-pin"))
        heading = "Введи код из Telegram"
    else:
        content = ("<p>Получи ссылку на бота. Код из личного чата введи в кабинете.</p>" + begin)
        heading = "Подключи Telegram"
    if pending:
        cancel = form(request, ROOT + "/revoke", '<button class="quiet">Отменить привязку</button>')
        refresh = (form(request, ROOT + "/begin", '<button class="quiet">Получить новую ссылку</button>')
                   if pending["attempts"] < 6 else "")
        if pending["attempts"] >= 6:
            content = '<p>Попытки ввода исчерпаны. Получи новую ссылку и код.</p>' + content
        content += f'<div class="dean-action-row">{refresh}{cancel}</div>'
    primary = panel(heading, content)
    return f'<div class="dean-flow-split">{primary}{chat_preview(staff)}</div>'


def index(request, db, *, message=""):
    user = request.user
    staff = user["role"] == "staff"
    config = TelegramConfig.from_env()
    attached = telegram.active_binding(db, user["id"])
    pending = telegram.pending_pair(db, user, config.name)
    body = back("/staff" if staff else "/profile", "К кабинету" if staff else "К профилю")
    state = f"@{config.name}" if config.name else "Подключается администратором"
    body += (f'<div class="dean-telegram-hero"><img src="{asset_url("telegram-avatar.jpg")}"'
             ' alt="" width="64" height="64"><div><h2>Вишка в Telegram</h2>'
             f'<p>{e(state)}</p></div></div>')
    expired = config.name and not attached and not pending and db.execute(
        "SELECT 1 FROM telegram_pair WHERE owner_id=? AND bot=? AND expires_at<=?",
        (user["id"], config.name, timestamp()),
    ).fetchone()
    if expired:
        body += flow_note("Срок привязки истёк", "Получи новую ссылку и открой личный чат.",
                          tone="attention", error=bool(message))
    elif message:
        next_step = ("Сверь код в личном чате и попробуй снова." if pending and pending["attempts"] < 6 else
                     "Получи новую ссылку, чтобы начать привязку.")
        body += flow_note(message, next_step, tone="error", error=True)
    if attached:
        body += linked(request, config, attached, staff)
    elif config.name:
        body += pairing(request, pending, staff)
    else:
        action = link("/staff" if staff else "/notifications",
                      "К очереди заявок" if staff else "К уведомлениям", "dean-action-link")
        offline = flow_note("Канал Telegram пока недоступен", "Все обновления доступны в кабинете.", action, tone="attention")
        body += f'<div class="dean-flow-split"><div>{offline}</div>{chat_preview(staff)}</div>'
    return page(request, "Telegram", body, "Расписание и уведомления в личном чате", section="telegram")


def reveal(request, config, nonce):
    href = f"https://t.me/{config.name}?start={nonce}"
    actions = (link(href, "Открыть бота в Telegram", "dean-action-link is-primary")
               + link(ROOT, "Вернуться к подтверждению", "dean-action-link"))
    body = panel("Открой личный чат",
                 '<p class="meta">Шаг 1 из 2</p><p>Открой бота и нажми «Запустить». '
                 'Ссылка для привязки действует 10 минут.</p>'
                 f'<div class="dean-action-row">{actions}</div>')
    return page(request, "Начало привязки", back(ROOT, "К настройкам Telegram") + body, section="telegram")
