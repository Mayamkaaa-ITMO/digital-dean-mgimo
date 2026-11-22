"""Navigation and reusable visual components for the Railway interface."""

from html import escape as e
from zoneinfo import ZoneInfo

from .clock import now
from .static_assets import asset_url


# Lucide outline icons. License in assets/Lucide-LICENSE.txt.
ICONS = {
    "brand": '<path d="M4 5h16M4 12h16M4 19h16M8 2v6m8 1v6m-6 1v6"/>',
    "today": '<rect x="3" y="5" width="18" height="16" rx="2"/><path d="M16 3v4M8 3v4M3 10h18m-13 5h3"/>',
    "calendar": '<rect x="3" y="5" width="18" height="16" rx="2"/><path d="M16 3v4M8 3v4M3 10h18M8 15h2m4 0h2m-8 3h2m4 0h2"/>',
    "book": '<path d="M12 7v14M3 18V5a2 2 0 0 1 2-2h3a4 4 0 0 1 4 4 4 4 0 0 1 4-4h3a2 2 0 0 1 2 2v13h-5a4 4 0 0 0-4 3 4 4 0 0 0-4-3Z"/>',
    "services": '<rect width="7" height="7" x="3" y="3" rx="1"/><rect width="7" height="7" x="14" y="3" rx="1"/><rect width="7" height="7" x="3" y="14" rx="1"/><rect width="7" height="7" x="14" y="14" rx="1"/>',
    "more": '<circle cx="5" cy="12" r="1"/><circle cx="12" cy="12" r="1"/><circle cx="19" cy="12" r="1"/>',
    "tasks": '<rect x="4" y="4" width="16" height="17" rx="2"/><path d="m8 13 2 2 5-5M9 3h6v4H9z"/>',
    "search": '<circle cx="11" cy="11" r="6"/><path d="m16 16 4 4"/>',
    "bell": '<path d="M18 8a6 6 0 0 0-12 0c0 7-3 7-3 9h18c0-2-3-2-3-9M10 21h4"/>',
    "profile": '<circle cx="12" cy="8" r="4"/><path d="M4 21a8 8 0 0 1 16 0"/>',
    "help": '<circle cx="12" cy="12" r="9"/><path d="M9 9a3 3 0 1 1 5 2c-1 .7-2 1-2 3m0 3h.01"/>',
    "file": '<path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8Zm0 0v6h6M8 13h8m-8 4h6"/>',
    "building": '<path d="M3 21h18M5 21V7l7-4 7 4v14M9 21v-4h6v4M9 9h.01M15 9h.01M9 13h.01M15 13h.01"/>',
    "source": '<ellipse cx="12" cy="5" rx="8" ry="3"/><path d="M4 5v14a8 3 0 0 0 16 0V5M4 12a8 3 0 0 0 16 0"/>',
    "theme": '<path d="M12 3a9 9 0 1 0 9 9c0-1.1-.9-2-2-2h-1a2 2 0 0 1-2-2V6a3 3 0 0 0-4-3Z"/><path d="M7.5 11h.01M9 7.5h.01M8.5 16h.01m4 2h.01"/>',
    "settings": '<path d="M12 2v4m0 12v4M2 12h4m12 0h4M5 5l3 3m8 8 3 3m0-14-3 3m-8 8-3 3"/><circle cx="12" cy="12" r="6"/><circle cx="12" cy="12" r="2"/>',
    "arrow": '<path d="M5 12h14m-6-6 6 6-6 6"/>',
    "left": '<path d="M19 12H5m6-6-6 6 6 6"/>',
    "send": '<path d="M12 19V5m-6 6 6-6 6 6"/>',
    "mic": '<rect x="9" y="2.5" width="6" height="12" rx="3"/><path d="M5.5 10v1.5a6.5 6.5 0 0 0 13 0V10M12 18v3.5m-3 0h6"/>',
    "stop": '<rect x="6.5" y="6.5" width="11" height="11" rx="2"/>',
    "expand": '<path d="M13 5h6v6m0-6-8 8M19 13v5a1 1 0 0 1-1 1H6a1 1 0 0 1-1-1V6a1 1 0 0 1 1-1h5"/>',
    "minimize": '<path d="M5 12h14"/>',
    "chevron-left": '<path d="m14 6-6 6 6 6"/>',
    "chevron-right": '<path d="m10 6 6 6-6 6"/>',
    "diagonal": '<path d="M7 17 17 7M7 7h10v10"/>',
    "map": '<path d="m3.5 6 5-2.5 7 3 5-2.5v14l-5 2.5-7-3-5 2.5V6Zm5-2v13.5m7-11v13.5"/>',
    "language": '<path d="M3 6h11M8 3v3m4 0c-.8 5-4 8-8 10m2-7c.8 2.1 2.4 4.2 4.5 5.5M13 21l4-9 4 9m-6.7-3h5.4"/>',
    "math": '<path d="M19 4H5l7 8-7 8h14"/>',
    "lab": '<path d="M9 3h6m-5 0v7l-5 8a2 2 0 0 0 1.7 3h10.6a2 2 0 0 0 1.7-3l-5-8V3M8 15h8"/>',
    "qr": '<path d="M3 3h6v6H3zM15 3h6v6h-6zM3 15h6v6H3zM15 15h3v3h3v3m-6 0v-3m6-6v3m-9-6v3m-9 0h3"/>',
    "library": '<path d="M4 5h3v14H4zM9 5h3v14H9zM15 6l3-1 4 13-3 1z"/>',
    "consultation": '<path d="M20 11.5a7.5 7.5 0 0 1-7.5 7.5H9l-4 2v-5l-.5-.6a7.5 7.5 0 1 1 15.5-3.9ZM8 11h8m-8 3h5"/>',
    "sport": '<path d="M6 7v10m12-10v10M3 10v4m18-4v4M6 12h12"/>',
    "menu": '<path d="M4 6h16M4 12h16M4 18h16"/>',
    "wallet": '<rect x="3" y="5" width="18" height="14" rx="2"/><path d="M3 10h18m-13 5h3"/>',
}

STUDENT = (
    ("/", "Сегодня", "today", ()),
    ("/schedule", "Расписание", "calendar", ("/calendar", "/events", "/universities")),
    ("/grades", "Учёба", "book", ("/study", "/work", "/curriculum", "/retakes")),
    ("/all-services", "Сервисы", "services", ("/student-services", "/services", "/requests", "/documents",
     "/bookings", "/bookables", "/slots", "/library", "/equipment", "/finance", "/residence", "/activities", "/campus-map",
     "/practice", "/opportunities", "/portfolio", "/courses", "/clubs", "/teams",
     "/campus-lab", "/campus-occupancy", "/campus-directory", "/campus-pass", "/coworking")),
)
STUDENT_MORE = (
    ("/assistant", "ИИ-помощник", "help", ()),
    ("/onboarding", "Первые шаги", "book", ()),
    ("/tasks", "Задачи", "tasks", ("/notes",)),
    ("/search", "Поиск", "search", ()),
    ("/notifications", "Уведомления", "bell", ("/announcements",)),
    ("/profile", "Профиль", "profile", ()),
    ("/lecture-library", "Лекции", "book", ("/lectures",)),
    ("/support", "Поддержка", "help", ()),
)
STAFF = (
    ("/staff", "Очередь", "today", ("/requests", "/staff/requests")),
    ("/staff/work", "Работы", "book", ()),
    ("/staff/manage", "Управление", "services", ()),
    ("/staff/bookings", "Записи", "calendar", ("/staff/slots", "/staff/bookables")),
)
STAFF_MORE = (
    ("/assistant", "ИИ-помощник", "help", ()),
    ("/onboarding", "Первые шаги", "book", ()),
    ("/staff/announcements", "Объявления", "bell", ("/announcements",)),
    ("/profile/telegram", "Telegram", "bell", ()),
    ("/staff/grades", "Оценки", "book", ("/staff/retakes", "/staff/attendance")),
    ("/staff/equipment", "Техника", "building", ()),
    ("/staff/library", "Библиотека", "book", ()),
    ("/campus-map", "Карта кампуса", "map", ()),
    ("/lecture-library", "Лекции", "book", ("/lectures",)),
    ("/staff/support", "Поддержка", "help", ()),
    ("/profile/sessions", "Сеансы", "profile", ()),
)
OPERATOR = ("/staff/public", "Источники", "source", ())
GUEST = (
    ("/universities", "Расписание вузов", "calendar", ("/guest",)),
    ("/login", "Вход", "profile", ()),
)


def icon(name):
    return ('<svg class="rw-icon" width="20" height="20" viewBox="0 0 24 24" fill="none" '
            'stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" '
            f'aria-hidden="true">{ICONS[name]}</svg>')


def brand(home, *, mobile=False):
    short = '<span class="dean-brand-short" aria-hidden="true">Деканат</span>' if mobile else ""
    mark = (f'<span class="dean-brand-mark"><img src="{asset_url("assistant-mascot.svg")}"'
            ' width="25" height="25" alt=""></span>')
    return (f'<a class="rw-brand" href="{home}" aria-label="Цифровой деканат">{mark}'
            f'<span class="dean-brand-name">Цифровой деканат</span>{short}</a>')


def current(path, item):
    href, _, _, family = item
    roots = family if href == "/staff" else family + (href,)
    return path == href or any(path == root or path.startswith(root + "/") for root in roots)


def nav_item(item, path, *, mobile=False, unread=False):
    href, label, glyph, _ = item
    active = ' aria-current="page"' if path == href else ' aria-current="location"' if current(path, item) else ""
    if mobile and href == "/schedule":
        href += "?view=list"
    indicator = '<i class="rw-notification-dot" aria-hidden="true"></i>' if unread else ""
    caption = f' aria-label="{label} · есть непрочитанные"' if unread else ""
    return f'<a href="{href}"{active}{caption}>{icon(glyph)}<span>{label}</span>{indicator}</a>'


def navigation(request, *, signout=""):
    user, path = request.user, request.path
    demo = getattr(request, "browser_demo", False)
    edition = demo or getattr(request, "product_design", False)
    if not user:
        main, more, home, context = GUEST, (), "/guest", "Открытый доступ"
        if demo:
            home = "/demo"
            main = (("/demo", "Демо", "services", ()),) if path in {"/demo", "/login"} else (
                ("/demo", "Демо", "services", ()), *main
            )
    elif user["role"] == "staff":
        main, more, home, context = STAFF, STAFF_MORE, "/staff", "Кабинет сотрудника"
        from .room_occupancy import available as occupancy_available
        if occupancy_available(user):
            more += (("/campus-occupancy", "Загруженность аудиторий", "building", ()),)
        if getattr(request, "lab_mode", False):
            more += (("/campus-lab", "Кампус", "building",
                      ("/coworking", "/campus-directory")),)
        if getattr(request, "railway_operator", False):
            more += (OPERATOR,)
    else:
        main, more, home, context = STUDENT, STUDENT_MORE, "/", "Кабинет студента"
    links = "".join(nav_item(item, path) for item in main)
    secondary = "".join(nav_item(item, path) for item in more)
    desktop_secondary = "".join(nav_item(item, path) for item in more if item[0] != "/profile")
    desktop_footer = f'<p class="rw-role">{context}</p>'
    shortcuts = ""
    pay_link = ""
    if user and user["role"] == "student":
        from . import campus_pay
        if campus_pay.available(user):
            pay_link = nav_item((campus_pay.PATH, campus_pay.LABEL, "wallet", ()), path)
        shortcuts = "".join(nav_item(item, path) for item in (
            ("/requests", "Заявки", "tasks", ("/services",)),
            ("/documents", "Документы", "file", ()),
            ("/bookings", "Бронирования", "calendar", ("/bookables", "/slots")),
        ))
        if edition:
            shortcuts += nav_item(("/tasks", "Задачи", "tasks", ("/notes",)), path)
            desktop_secondary = "".join(nav_item(item, path) for item in more
                                        if item[0] in {"/lecture-library", "/notifications"})
            desktop_footer = ('<nav class="dean-rail-footer" aria-label="Помощь">'
                              + "".join(nav_item(item, path) for item in more if item[0] in {"/onboarding", "/support"})
                              + '</nav>')
        shortcuts += pay_link
    shortcut_group = f'<div class="rw-rail-shortcuts">{shortcuts}</div>' if shortcuts else ""
    shared = bool(user and edition)
    rail_id = ' id="dean-navigation" data-dean-navigation' if shared else ""
    close = ('<a class="dean-nav-close" href="#main" data-dean-nav-close aria-label="Закрыть меню">×</a>'
             if shared else "")
    footer = ""
    if shared:
        demo_role = nav_item(("/demo", "Роли демо", "profile", ()), path) if demo else ""
        footer = f'<div class="dean-nav-account">{demo_role}{signout}</div>'
    desktop = (f'<aside class="rw-rail rw-only"{rail_id}>{brand(home)}{close}<nav aria-label="Основные разделы">'
               f'<div class="rw-rail-primary">{links}</div>{shortcut_group}'
               f'{f"<div>{desktop_secondary}</div>" if desktop_secondary else ""}</nav>'
               f'{desktop_footer}{footer}</aside>')
    if shared:
        desktop += ('<a class="dean-nav-shade" href="#main" tabindex="-1" aria-hidden="true" '
                    'data-dean-nav-close></a>')
    if shared or not more:
        return home, desktop, ""
    active = ' class="is-current"' if any(current(path, item) for item in more) else ""
    demo_item = nav_item(("/demo", "Роли демо", "profile", ()), path) if demo else ""
    if user["role"] == "student":
        applications = (("/requests?mode=catalog", "Заявки", "tasks", ()),
                        ("/requests", "Мои заявки", "file", ()))
        request_path = "/requests?mode=catalog" if path == "/requests" and request.query("mode") == "catalog" else path
        deanery = "".join(nav_item(item, request_path) for item in applications)
        assistant = nav_item(("/assistant", "Открыть Вишку", "help", ()), path)
        mobile_order = ("/tasks", "/lecture-library", "/notifications", "/search",
                        "/profile", "/support", "/onboarding")
        menu_items = {item[0]: item for item in more}
        shortcuts = "".join(nav_item(menu_items[href], path,
                                      unread=href == "/notifications" and getattr(request, "has_unread", False))
                            for href in mobile_order)
        secondary = (f'<section class="rw-more-deanery"><h3>Деканат</h3><div>{deanery}</div></section>'
                     f'<div class="rw-more-essentials">{shortcuts}{pay_link}{demo_item}</div>'
                     f'<div class="rw-more-assistant">{assistant}</div>'
                     f'{signout}')
    else:
        secondary = f'<div class="rw-more-staff">{secondary}{demo_item}</div>{signout}'
    flyout = ('<details class="rw-more" data-appearance-flyout data-more-menu>'
              f'<summary{active} aria-controls="more-menu">{icon("more")}<span>Ещё</span></summary>'
              '<div class="rw-more-sheet" id="more-menu" role="dialog" aria-modal="true" aria-labelledby="more-menu-title">'
              '<header class="rw-more-title"><h2 id="more-menu-title">Ещё</h2>'
              '<button type="button" class="quiet rw-more-dismiss" data-more-dismiss aria-label="Закрыть" hidden>×</button></header>'
              f'{secondary}</div></details>')
    mobile_links = "".join(nav_item(item, path, mobile=True) for item in main)
    mobile = f'<nav class="rw-dock rw-only" aria-label="Основные разделы">{mobile_links}{flyout}</nav>'
    return home, desktop, mobile


def service_tile(href, title, summary, glyph, style=""):
    return (f'<a class="rw-service-tile {style}" href="{e(href)}" data-glyph="{e(glyph)}">'
            f'<span class="rw-tile-icon">{icon(glyph)}</span><strong>{e(title)}</strong>'
            f'<span>{e(summary)}</span><span class="rw-tile-arrow">{icon("arrow")}</span></a>')


def subject_icon(subject):
    name = subject.casefold()
    families = (
        ("source", ("базы данных", "база данных")),
        ("language", ("язык", "английск", "language", "english")),
        ("math", ("математик", "алгебр", "анализ")),
        ("lab", ("лаборатор", "химия")),
    )
    return next((glyph for glyph, names in families if any(word in name for word in names)), "book")


def lesson_type(label):
    key = label.lower()
    tones = (("лекц", "lecture"), ("практ", "practice"), ("семин", "practice"), ("лаб", "lab"))
    tone = next((value for prefix, value in tones if key.startswith(prefix)), "other")
    return f'<span class="rw-lesson-type" data-lesson-type="{tone}">{e(label)}</span>'


def count_label(number, one, few, many):
    ending = number % 10
    word = many if 11 <= number % 100 <= 14 else one if ending == 1 else few if 2 <= ending <= 4 else many
    return f"{number} {word}"


def today_clock(zone, mode):
    local = now().astimezone(ZoneInfo(zone))
    label = "МСК" if zone == "Europe/Moscow" else local.strftime("%Z")
    caption = "Время в демо" if mode == "demo" else "Время вуза"
    return (f'<div class="rw-v29-only rw-v29-clock"><div><time datetime="{local.isoformat()}">'
            f'{local:%H:%M} {e(label)}</time><small>{caption}</small></div>'
            f'<a href="/profile/dashboard?mode={mode}" aria-label="Настроить главную">{icon("settings")}</a></div>')


def lesson_lead(caption, href, title, start, end, kind, room, changed, *, note="", old_room="", preview=""):
    status = "Перенос аудитории" if old_room else "Занятие изменено" if changed else ""
    history = f'Перенос из {old_room}' if old_room else status
    revision = f'<span class="rw-lead-change">{e(history)}</span>' if history else ""
    state = f'<span class="rw-lead-state">{e(note)}</span>' if note else ""
    return (f'<a class="rw-lead" href="{e(href)}" aria-label="{e(title)}"{preview}><span class="rw-lead-caption">{e(caption)}</span>'
            f'{icon("arrow")}<div class="rw-lead-time"><strong>{e(start)}</strong><span>до {e(end)}</span></div>'
            f'<div class="rw-lead-details"><strong>{e(title)}</strong><span class="rw-lead-kind">{e(kind)}</span>'
            f'<span class="rw-lead-location">{icon("map")}{e(room or "Аудитория не указана")}</span>{state}{revision}</div></a>')


def priority_link(href, title, subtitle, glyph, *, tone=""):
    return (f'<a class="rw-priority-link" data-glyph="{e(glyph)}" data-tone="{e(tone)}" href="{e(href)}">{icon(glyph)}'
            f'<span><strong>{e(title)}</strong><small>{e(subtitle)}</small></span>{icon("arrow")}</a>')
