from datetime import timedelta
import json
from urllib.parse import urlencode
from zoneinfo import ZoneInfo

from . import access_paths, calendar_share, calendar_tools, photo_import, study_context
from .clock import now
from .domain import role, timestamp
from .schedule import local_value
from .ui import back, button, e, empty, form, hidden, input_field, link, local_label, page, panel

ROOT = "/calendar/share"
PUBLIC = "/calendar/shared/"


def details(item, zone):
    start = local_value(item["start"],item["zone"] if item["day"] else zone)
    end = local_value(item["end"],zone)
    end_label = end.strftime("%H:%M" if end.date() == start.date() else "%d.%m · %H:%M")
    time = f"{start:%d.%m} · весь день" if item["day"] else f"{start:%d.%m · %H:%M}–{end_label}"
    return time + (f' · {item["place"]}' if item["place"] else "")


def period(first, zone):
    zone_label = "МСК" if zone == "Europe/Moscow" else zone
    last = first + timedelta(days=6)
    return f"{first:%d.%m}–{last:%d.%m.%Y} · {zone_label}"


def index(request,db):
    role(request.user,"student")
    zone = calendar_tools.period_for(request.user,request.public).timezone
    today = now().astimezone(ZoneInfo(zone)).date()
    week = request.query("week",today.isoformat())
    first,zone,events = calendar_share.choices(db,request.user,request.public,week)
    body = back("/schedule", "К расписанию")
    date = (f'<form method="get" action="{ROOT}" class="inline dean-share-date">'
            + input_field("week", "Дата в неделе", first.isoformat(), "required", "date")
            + '<button class="quiet">Показать неделю</button></form>')
    if events:
        fields = hidden("week",first)
        fields += hidden("context_revision",study_context.preference(db,request.user)[1])
        checks = "".join(
            f'<label class="check"><input type="checkbox" name="event" value="{key}">'
            f'<span>{e(item["title"])}<small class="meta"> · {e(details(item,zone))}</small></span></label>'
            for key,item in events.items()
        )
        fields += ('<fieldset><legend>События для ссылки</legend>' + checks + "</fieldset>"
                   '<p class="meta" data-share-count role="status" hidden></p>')
        content = panel("Выбери неделю и события", date + form(request, ROOT, fields + button("Создать ссылку"),
                        command=True, style="dean-share-selection"))
    else:
        actions = (link("/events/new", "Добавить вручную", "dean-action-link is-primary")
                   + link("/calendar/import/photo", "Добавить по фото", "dean-action-link"))
        content = panel("Выбери неделю", date
                        + '<h3>На этой неделе нет личных событий</h3>'
                          '<p class="meta">Добавь событие, затем вернись к этой неделе.</p>'
                        + f'<div class="dean-action-row">{actions}</div>')
    rows = []
    for row in calendar_share.mine(db,request.user):
        active = not row["revoked_at"] and row["expires_at"]>timestamp()
        expires = (f'Действует до {local_label(row["expires_at"], zone)}' if active else
                   "Доступ отозван" if row["revoked_at"] else "Срок истёк")
        action = form(request, f'{ROOT}/{row["id"]}/revoke', '<button class="quiet">Отозвать</button>') if active else ""
        rows.append(f'<li><div><strong>Неделя с {e(photo_import.monday(row["monday"]).strftime("%d.%m.%Y"))}</strong>'
                    f'<p class="meta">{e(expires)}</p></div>{action}</li>')
    guidance = panel("Кто увидит неделю",
                     "<p>Все, у кого есть ссылка, увидят только выбранные личные события.</p>"
                     '<p class="meta">Ссылка действует 7 дней. Это снимок недели на момент создания. '
                     'После отзыва уже скопированные события останутся в личных календарях.</p>',
                     style="dean-flow-aside")
    mine = panel("Мои ссылки", f'<ul class="rows results">{"".join(rows)}</ul>' if rows else
                 empty("Созданных ссылок пока нет."), style="dean-share-mine")
    body += f'<div class="dean-flow-split">{content}<div class="dean-share-content">{guidance}{mine}</div></div>'
    return page(request, "Поделиться расписанием", body, period(first, zone), section="calendar-share")


def reveal(request, token):
    url = request.origin + PUBLIC + token
    actions = '<button type="button" data-copy-share hidden>Скопировать ссылку</button>'
    if request.secure:
        actions += link("https://t.me/share/url?"+urlencode({"url":url,"text":"Расписание на неделю"}),
                        "Поделиться в Telegram", "dean-action-link")
    actions += (f'<a href="{e(PUBLIC + token)}" class="dean-action-link" target="_blank" rel="noopener">'
                'Открыть общую страницу в новой вкладке</a>')
    body = panel("Ссылка готова", "<p>Скопируй её сейчас. В разделе «Мои ссылки» можно отозвать доступ.</p>"
                 f'<label>Ссылка на расписание<input id="calendar-share-url" readonly value="{e(url)}"></label>'
                 f'<div class="dean-action-row">{actions}</div><p role="status" data-copy-status></p>',
                 style="dean-share-reveal")
    return page(request, "Поделиться расписанием", body + back(ROOT, "К моим ссылкам"), section="calendar-share")


def public(request,db,token):
    row = calendar_share.shared(db,token)
    values = json.loads(row["events_json"])
    rows = "".join(f'<li><div><strong>{e(item["title"])}</strong>'
                   f'<p class="meta">{e(details(item,row["timezone"]))}</p></div></li>' for item in values)
    first = photo_import.monday(row["monday"])
    events = f'<ul class="rows">{rows}</ul>' if rows else empty("Пока пусто.")
    action = ""
    if not request.user:
        action = link(access_paths.login_url(PUBLIC + token), "Войти и добавить к себе", "dean-action-link is-primary")
    elif request.user["role"] == "student" and request.user["id"] != row["owner_id"]:
        action = form(request, PUBLIC + token + "/copy", button("Подготовить импорт к себе"), command=True)
    body = panel("События недели", events + action, style="dean-shared-events")
    return page(request, "Расписание по ссылке", body, period(first, row["timezone"]), section="calendar-shared")
