from html import escape
from hmac import compare_digest

from . import appearance
from .auth import csrf_token
from .domain import identifier
from .schedule import local_value
from .static_assets import asset_url


CONFIRMATION_TEXT = {
    "booking": "Запись подтверждена",
    "request": "Заявка отправлена",
    "equipment": "Заявка на технику оформлена",
    "library": "Книга зарезервирована",
    "wait": "Ты в списке ожидания",
    "work": "Работа отправлена",
    "calendar": "Расписание добавлено",
    "course": "Запись на курс подтверждена",
    "assistant": "Действие выполнено",
}


def confirmation(request, path, kind):
    name = request.session_cookie + "_confirmation"
    signature = csrf_token(f"{request.session}|{path}|confirmation|{kind}")
    request.set_cookie(name, f"{kind}.{signature}", 90)


def confirmation_notice(request):
    if not request.user or request.method != "GET":
        return ""
    name = request.session_cookie + "_confirmation"
    kind, _, signature = request.cookie(name).partition(".")
    if kind not in CONFIRMATION_TEXT or not signature.isascii() or len(signature) != 64:
        return ""
    expected = csrf_token(f"{request.session}|{request.path}|confirmation|{kind}")
    if not compare_digest(signature, expected):
        return ""
    request.set_cookie(name, "", 0)
    return ('<section class="dean-confirmation" data-confirmation role="status">'
            f'<img src="{asset_url("assistant-mascot.svg")}" alt="" width="54" height="54">'
            f'<strong>{e(CONFIRMATION_TEXT[kind])}</strong>'
            '<button class="quiet" type="button" data-confirmation-dismiss hidden aria-label="Закрыть подтверждение">×</button>'
            '</section>')

NAV = (
    ("/", "Сегодня"), ("/schedule", "Расписание"), ("/grades", "Успеваемость"),
    ("/requests", "Заявления"), ("/notifications", "Уведомления"), ("/all-services", "Все сервисы"),
    ("/search", "Поиск"), ("/assistant", "ИИ-помощник"), ("/onboarding", "Первые шаги"),
)
MONTHS = ("января", "февраля", "марта", "апреля", "мая", "июня", "июля", "августа",
          "сентября", "октября", "ноября", "декабря")
WEEKDAYS = ("Понедельник", "Вторник", "Среда", "Четверг", "Пятница", "Суббота", "Воскресенье")
SHORT_DAYS = ("Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс")


def e(value):
    return escape(str(value), quote=True)


def calendar_heading(day, lessons, changes):
    week_day = WEEKDAYS[day.weekday()]
    count = appearance.count_label(lessons, "пара", "пары", "пар")
    transfer = " · " + appearance.count_label(changes, "перенос", "переноса", "переносов") if changes else ""
    return (f'<div class="dean-today-date" aria-hidden="true">'
            f'<span class="dean-date-number">{day.day}</span><span class="dean-date-caption">'
            f'<strong>{week_day}</strong><small>{MONTHS[day.month - 1]} · {count}{transfer}</small></span></div>')


def page(request, title, body, subtitle="", *, section="", raspay=False, heading_action="", context_group="",
         dashboard_chat="", heading_detail="", breadcrumb=""):
    user = request.user
    browser_demo = getattr(request, "browser_demo", False)
    product = getattr(request, "product_design", False)
    edition = browser_demo or product
    nav, actions, notifications, search, signout = "", "", "", "", ""
    if user:
        if user["role"] == "staff":
            links = [("/staff", "Кабинет сотрудника"), ("/assistant", "ИИ-помощник"), ("/onboarding", "Первые шаги")]
        else:
            links = NAV
        nav = '<nav class="nav" aria-label="Разделы">' + "".join(
            f'<a href="{href}"{" aria-current=\"page\"" if request.path == href else ""}>{label}</a>'
            for href, label in links
        ) + "</nav>"
        next_page = hidden("next", "/demo") if browser_demo else ""
        signout = form(request, "/logout", next_page + '<button class="quiet">Выйти</button>', style="logout")
        actions = signout
        if user["role"] == "student":
            username = user["username"]
            has_photo = getattr(request, "has_profile_photo", False)
            demo_login = username if edition and not has_photo and username in {"student-a", "student-b"} else ""
            demo_class = " dean-demo-profile" if demo_login else ""
            demo_label = (f'<small class="dean-demo-profile-login">{e(demo_login)}</small>' if demo_login else "")
            demo_description = (f' aria-description="Текущий аккаунт {e(demo_login)}"' if demo_login else "")
            profile_icon = ('<img class="dean-header-photo" src="/profile/photo" alt="" width="32" height="32">'
                            if has_photo else appearance.icon("profile"))
            profile = (f'<a class="ui-profile{demo_class}" href="/profile" aria-label="Профиль"'
                       f'{demo_description}>'
                       f'<span>Профиль</span><span class="rw-only ui-profile-icon">{profile_icon}</span>'
                       f'{demo_label}</a>')
            actions = profile + actions
            unread = getattr(request, "has_unread", False)
            caption = "Уведомления · есть непрочитанные" if unread else "Уведомления"
            dot = '<i class="rw-notification-dot" aria-hidden="true"></i>' if unread else ""
            notifications = (f'<a class="rw-only rw-notification-button" href="/notifications" aria-label="{caption}">'
                             f'{appearance.icon("bell")}{dot}</a>')
            search = (f'<a class="rw-v29-only rw-v29-search" href="/search">'
                      f'{appearance.icon("search")}<span>Поиск</span></a>')
            if edition:
                search = (f'<a class="rw-only dean-global-search" href="/search" aria-label="Поиск по деканату">'
                          f'{appearance.icon("search")}</a>')
        actions = f'<div class="account-actions">{actions}</div>'
    selection = getattr(request, "public", None)
    public_context = "Демонстрационные учебные данные"
    mobile_context = "Демо"
    if selection:
        is_public_view = (request.path in {"/", "/schedule", "/search", "/notifications"}
                          or request.path.startswith(("/schedule/public/", "/schedule/rooms/", "/schedule/room/",
                                                      "/schedule/raspay", "/study/public/"))
                          or request.path == "/student-services" and request.query("mode") != "demo")
        if is_public_view:
            public_context = "Публичные учебные данные · личный кабинет"
            mobile_context = "Открытое расписание"
        else:
            public_context = "Учебный стенд · вымышленные данные"
    description = f'<p class="subtitle">{e(subtitle)}</p>' if subtitle else ""
    home, rail, dock = appearance.navigation(request, signout=signout)
    shared_shell = bool(user and edition)
    nav_toggle = (f'<a class="dean-nav-toggle" href="#dean-navigation" aria-label="Меню" '
                  f'aria-controls="dean-navigation" data-dean-nav-toggle>{appearance.icon("menu")}</a>'
                  if shared_shell else "")
    guest_link = ""
    if browser_demo and request.path != "/demo":
        guest_link = link("/demo", "Роли", "dean-demo-link")
    elif not user and not browser_demo and request.path != "/login":
        guest_link = link("/login", "Вход", "rw-only rw-guest-link")
    assistant = ""
    if user:
        assistant_mark = f'<img src="{asset_url("assistant-mascot.svg")}" width="25" height="25" alt="">'
        assistant = (f'<a class="dean-header-assistant" href="/assistant" aria-label="Вишка" data-tour-assistant>'
                     f'{assistant_mark}'
                     '<span>Вишка</span></a>')
    calendar_assets = (f'<link rel="stylesheet" href="{asset_url("raspay.css")}">'
                       f'<script src="{asset_url("raspay.js")}" defer></script>') if raspay else ""
    access_assets = (f'<link rel="stylesheet" href="{asset_url("access.css")}">'
                     f'<script src="{asset_url("auth.js")}" defer></script>') if not raspay else ""
    navigation_assets = f'<link rel="stylesheet" href="{asset_url("navigation.css")}">' if user else ""
    campus_assets = (f'<link rel="stylesheet" href="{asset_url("campus-map.css")}">'
                     f'<script src="{asset_url("campus-map.js")}" defer></script>') if section == "campus-map" else ""
    legacy_nav = f'<div class="nav-wrap legacy-only"><div class="wrap">{nav}</div></div>' if nav and not raspay else ""
    from os import getenv
    lab = getenv("DEAN_CAMPUS_LAB") == "1"
    path = request.path
    lab_interactive = section == "campus-occupancy" or (lab and (
        path in {"/campus-lab/terminal", "/campus-directory"}
        or path.startswith("/campus-pay/")
        or (section == "lecture-library" and path.startswith("/lectures/"))
    ))
    lab_styles = lab_interactive or (lab and (
        section in {"campus-lab", "activities"} or path == "/staff"
        or path == "/coworking" or path.startswith("/coworking/")
    ))
    lab_assets = f'<link rel="stylesheet" href="{asset_url("campus-lab.css")}">' if lab_styles else ""
    if lab_interactive:
        lab_assets += f'<script defer src="{asset_url("campus-lab.js")}"></script>'
    if lab and user and request.path != "/campus-lab" and not raspay and not edition:
        body = '<p><a class="dean-action-link" href="/campus-lab">Открыть кампус</a></p>' + body
    private = (' data-private' if user else '') + (' data-browser-demo' if edition else '')
    if shared_shell:
        private += ' data-responsive-shell'
    if edition and user and user["role"] == "student":
        private += ' data-student-shell'
    body_class = ' class="rp"' if raspay else ''
    heading = '' if raspay or section in {"login", "schedule"} else f'<h1>{e(title)}</h1>{description}{heading_detail}'
    if heading_action:
        heading = f'<div class="rw-page-heading"><div>{heading}</div>{heading_action}</div>'
    content_class = "rp-wrap" if raspay else "wrap"
    page_title = f"{title} · Расписание" if raspay else title
    if not section and request.path == "/login":
        section = "login"
    notice = ""
    if user and request.cookie(request.remember_notice_cookie) == "session":
        request.set_cookie(request.remember_notice_cookie, "", 0)
        notice = ('<p role="status">Вход действует на текущий сеанс. У этого аккаунта нельзя запомнить устройство. '
                  'Закончив работу, нажми «Выйти».</p>')
    context = (f'<span class="ui-long-context">{e(public_context)}</span>'
               f'<span class="rw-only ui-mobile-context">{e(mobile_context)}</span>' if user else
               '<span class="legacy-only">Конкурсный прототип · вымышленные данные</span>'
               '<span class="rw-only">Открытые расписания вузов</span>')
    if browser_demo and request.path in {"/demo", "/login"}:
        context = '<span class="rw-only">Учебный стенд</span>'
    elif edition and user:
        caption = ("Кабинет сотрудника" if user["role"] == "staff" else context_group or "Кабинет студента")
        context = f'<span class="rw-only">{e(caption)}</span>'
    group_context = ""
    if context_group:
        indicator = '<span class="tag" data-tone="neutral">Демо</span>' if mobile_context.startswith("Демо") else ""
        group_context = f'<p class="rw-v29-only rw-v29-context">{e(context_group)}{indicator}</p>'
    overlay = ""
    if getattr(request, "access_offer", None):
        from .access_views import first_access
        overlay = first_access(request)
    elif getattr(request, "tour_step", 0):
        from .onboarding import overlay as tour_overlay
        overlay = tour_overlay(request)
    if dashboard_chat:
        body = ('<div class="dean-dashboard-layout"><div class="dean-dashboard-content">' + body
                + '</div>' + dashboard_chat + '</div>')
    assisted_assets = (f'<link rel="stylesheet" href="{asset_url("assisted.css")}">'
                       if dashboard_chat or section in {"assistant", "calendar-photo", "calendar-preview",
                                                       "calendar-share", "calendar-shared", "telegram"} else "")
    experience_assets = ""
    if overlay or dashboard_chat or section in {"assistant", "login", "access"}:
        experience_assets = f'<link rel="stylesheet" href="{asset_url("experience.css")}">'
        if overlay:
            experience_assets += f'<script src="{asset_url("experience.js")}" defer></script>'
    railway_loading = f' data-chat-owner="{e(user["id"])}"' if dashboard_chat else " defer"
    chat_assets = (f'<script src="{asset_url("chat.js")}" defer></script>'
                   f'<script src="{asset_url("assistant-voice.js")}" defer></script>'
                   if dashboard_chat or section == "assistant" else "")
    timetable_assets = (f'<link rel="stylesheet" href="{asset_url("schedule-layout.css")}">'
                        f'<script src="{asset_url("schedule-layout.js")}" defer></script>') if section == "schedule" else ""
    notification_assets = (f'<link rel="stylesheet" href="{asset_url("notifications.css")}">'
                           if section in {"notifications", "staff-announcements"} else "")
    edition_login = f'<link rel="stylesheet" href="{asset_url("edition-login.css")}">' if section == "login" else ""
    booking_assets = (f'<link rel="stylesheet" href="{asset_url("bookings.css")}">'
                      if section in {"bookings", "booking-detail", "booking-slot"} else "")
    if section == "booking-slot":
        booking_assets += f'<script src="{asset_url("bookings.js")}" defer></script>'
    library_assets = (f'<link rel="stylesheet" href="{asset_url("lecture-library.css")}">'
                      f'<script src="{asset_url("lecture-library.js")}" defer></script>') if section == "lecture-library" else ""
    sheet = lesson_sheet() if section in {"today", "schedule"} else ""
    privacy_footer = ('' if section in {"privacy", "assistant"} else '<footer class="meta"><a href="/privacy">'
                      'Политика обработки персональных данных</a></footer>')
    return f"""<!doctype html>
<html lang="ru" data-interface="railway" data-theme="raspay"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover, interactive-widget=resizes-content">
<meta name="theme-color" content="#f3f3f6">
<title>{e(page_title)} · Цифровой деканат</title>
<link rel="icon" type="image/svg+xml" href="{asset_url("assistant-mascot.svg")}">
<link rel="preload" href="{asset_url("nunito-cyrillic.woff2")}" as="font" type="font/woff2" crossorigin>
<script src="{asset_url("railway.js")}"{railway_loading}></script>
<link rel="stylesheet" href="{asset_url("main.css")}">{access_assets}{calendar_assets}
<link rel="stylesheet" href="{asset_url("railway.css")}">{assisted_assets}{navigation_assets}{campus_assets}{experience_assets}
<link rel="stylesheet" href="{asset_url("design-final.css")}">{timetable_assets}{library_assets}{notification_assets}
<script src="{asset_url("main.js")}" defer></script>{chat_assets}{lab_assets}
<link rel="stylesheet" href="{asset_url("edition.css")}">{edition_login}{booking_assets}<script src="{asset_url("edition.js")}" defer></script>
</head><body{body_class}{private}>
<a class="skip" href="#main">К содержимому</a>
<header class="ui-header"><div class="top wrap">{nav_toggle}<div class="ui-identity">
{f'<div class="rw-mobile-brand rw-only">{appearance.brand(home, mobile=True)}</div>' if not shared_shell else ''}
<div class="brand">Цифровой деканат</div>
<p class="context">{context}</p>{group_context}</div>
<div class="ui-tools">{guest_link}{assistant}{search}{notifications}{actions}</div></div>{legacy_nav}</header>
{rail}
<main id="main" class="{content_class}" data-section="{e(section)}" tabindex="-1">{breadcrumb}{heading}{notice}{confirmation_notice(request)}{body}{privacy_footer}</main>
{dock}
{overlay}
{sheet}
<div id="navigation-status" role="status" hidden>Открываем страницу…</div>
</body></html>"""


def lesson_sheet():
    return ('<dialog class="dean-lesson-sheet" data-lesson-sheet aria-labelledby="lesson-sheet-title">'
            '<div class="dean-sheet-grip" aria-hidden="true"></div>'
            '<header><div class="dean-sheet-badges"><span data-sheet-kind></span><span data-sheet-status></span></div>'
            '<button type="button" class="quiet" data-sheet-close aria-label="Закрыть" autofocus>×</button></header>'
            '<h2 id="lesson-sheet-title" data-sheet-title></h2>'
            f'<div class="dean-sheet-facts"><p>{appearance.icon("calendar")}<strong data-sheet-time></strong></p>'
            f'<p data-sheet-teacher-row>{appearance.icon("profile")}<span data-sheet-teacher></span></p>'
            f'<p data-sheet-location-row>{appearance.icon("map")}<span data-sheet-location></span></p></div>'
            '<p data-sheet-old-room></p>'
            '<p class="meta dean-sheet-map-note" data-sheet-map-unavailable hidden>Маршрут до этой аудитории недоступен.</p>'
            '<nav aria-label="Действия с парой">'
            f'<a class="rw-primary-link" data-sheet-map hidden>{appearance.icon("map")} Показать на карте</a>'
            '<a class="dean-sheet-detail" data-sheet-detail href="/schedule">Открыть пару</a></nav></dialog>')


def form(request, path, body, *, command=False, style="", multipart=False, recover=False):
    command_field = hidden("request_id", identifier()) if command else ""
    enctype = ' enctype="multipart/form-data"' if multipart else ""
    recoverable = ' data-application-form' if recover else ""
    if recover and request.user and path.startswith("/requests/") and path.endswith("/save"):
        recoverable += f' data-draft-owner="{e(request.user["id"])}"'
    feedback = ('<p data-form-result role="status" tabindex="-1" hidden></p>'
                '<button type="button" class="quiet" data-form-retry hidden>Повторить ту же попытку</button>') if recover else ""
    return (f'<form action="{e(path)}" method="post" class="{e(style)}"{enctype}{recoverable}>'
            f'{hidden("csrf", request.csrf)}{command_field}{body}{feedback}</form>')


def hidden(name, value):
    return f'<input type="hidden" name="{e(name)}" value="{e(value)}">'


def input_field(name, label, value="", options="", kind="text"):
    return (f'<label>{e(label)}<input type="{kind}" name="{name}" '
            f'value="{e(value)}" {options}></label>')


def textarea(name, label, limit, required=False, placeholder="", value=""):
    return (f'<label>{e(label)}<textarea name="{name}" maxlength="{limit}" '
            f'placeholder="{e(placeholder)}" {"required" if required else ""}>{e(value)}</textarea></label>')


def button(label):
    return f"<button>{e(label)}</button>"


def link(href, label, style=""):
    return f'<a href="{e(href)}" class="{e(style)}">{e(label)}</a>'


def linked_row(href, label, description):
    return (f'<li class="dean-linked-row"><a href="{e(href)}"><strong>{e(label)}</strong>'
            f'<span class="meta">{e(description)}</span></a></li>')


def panel(title, body, action="", *, style="", key=""):
    classes = f"panel {e(style)}" if style else "panel"
    anchor = f' id="{e(key)}"' if key else ""
    return f'<section class="{classes}"{anchor}><div class="panel-head"><h2>{e(title)}</h2>{action}</div>{body}</section>'


def flow_note(title, message="", action="", *, tone="info", error=False):
    focus = ' role="alert" tabindex="-1" data-restore-focus' if error else ""
    detail = f'<p>{e(message)}</p>' if message else ""
    return (f'<div class="dean-note" data-tone="{e(tone)}"{focus}><strong>{e(title)}</strong>'
            f'{detail}{action}</div>')


def empty(message):
    return f'<p class="empty">{e(message)}</p>'


def back(href, message):
    return f'<p class="back"><a href="{e(href)}" class="dean-secondary-action">{appearance.icon("chevron-left")}{e(message)}</a></p>'


def local_label(value, zone):
    moment = local_value(value, zone)
    return f"{moment:%d.%m.%Y, %H:%M}"


def day_label(day):
    return f"{day.day} {MONTHS[day.month - 1]}"


def select_field(name, label, choices, selected=""):
    options = "".join(f'<option value="{e(value)}"{" selected" if value == selected else ""}>{e(text)}</option>'
                      for value, text in choices)
    return f'<label>{e(label)}<select name="{e(name)}" aria-label="{e(label)}">{options}</select></label>'


