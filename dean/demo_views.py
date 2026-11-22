"""Entry points and sample campus pass for the local browser demo."""

from base64 import b64encode

from . import access_paths, appearance, event_tickets
from .domain import identifier
from .ui import back, e, form, hidden, link, page, panel


def role_card(request, role, title, description, glyph):
    if role == "guest":
        action = link("/universities", "Выбрать вуз", "dean-demo-action")
    else:
        destination = "/staff" if role == "staff" else "/"
        if not request.user:
            action = link(access_paths.login_url(destination), "Войти", "dean-demo-action")
        elif request.user["role"] == role:
            action = link(destination, "Открыть кабинет", "dean-demo-action")
        else:
            action = form(request, "/logout", hidden("next", "/demo")
                          + '<button class="quiet">Выйти из текущего кабинета</button>')
    return (f'<article class="dean-demo-card"><span class="dean-subject-icon">{appearance.icon(glyph)}</span>'
            f'<h2>{title}</h2><p>{description}</p>{action}</article>')


def index(request):
    roles = "".join(role_card(request, *item) for item in (
        ("student", "Студент", "Расписание, учёба, заявки и сервисы кампуса.", "book"),
        ("staff", "Сотрудник", "Очередь заявок, публикация событий и работа с группой.", "tasks"),
        ("guest", "Гость", "Открытое расписание и маршруты по кампусу.", "map"),
    ))
    examples = (
        ("Расписание и маршрут", "Выбери группу и занятие с аудиторией.", "/universities", "guest"),
        ("Перенос расписания с фото", "Проверь распознавание, исправь аудиторию и скачай .ics.", "/calendar/import/photo", "student"),
        ("Справка об обучении", "Студент подаёт заявку, сотрудник возвращает или выдаёт PDF.", "/requests?mode=catalog&category=documents", "student"),
        ("Событие и QR-билет", "Опубликуй событие и проверь билет у входа.", "/campus-lab/events", "staff"),
        ("Чат с Вишкой", "Спроси о записи или создай задачу с подтверждением.", "/assistant", "student"),
        ("Кампус Pay", "Посмотри баланс и оплати учебную покупку.", "/campus-pay", "student"),
        ("Счётчик коворкинга", "Прогони входы и выходы в репетиции.", "/coworking", "staff"),
        ("Загруженность аудиторий", "Сравни свободные места и тепловые карты помещений.", "/campus-occupancy", "student"),
        ("Эфир и учебная работа", "Открой эфир, сохрани запись и выдай задание.", "/lectures", "staff"),
        ("Поддержка", "Создай обращение из кабинета студента.", "/support", "student"),
    )
    items = []
    for title, description, target, role in examples:
        can_open = role == "guest" or bool(request.user and request.user["role"] == role)
        label = {"student": "Студент", "staff": "Сотрудник", "guest": "Гость"}[role]
        title_html = link(target, title) if can_open else f'<strong>{title}</strong>'
        items.append(f'<li><div>{title_html}<p>{description}</p></div><span>{label}</span></li>')
    current = ""
    if request.user:
        current = f'<p class="dean-demo-current">Вы вошли как <strong>{e(request.user["username"])}</strong></p>'
    body = (current + f'<div class="dean-demo-roles">{roles}</div>'
            + panel("Что можно проверить", f'<ul class="dean-demo-scenarios">{"".join(items)}</ul>'))
    return page(request, "Цифровой деканат", body, "Демо для телефона и компьютера", section="demo")


def campus_pass_available(request):
    from .seed import UNIVERSITY, seed_id

    user = request.user
    return bool(user and user["role"] == "student" and (
        request.browser_demo or (request.product_design and user["university_id"] == UNIVERSITY
                                 and user["id"] in {seed_id("student-a"), seed_id("student-b")})
    ))


def campus_pass(request):
    code = b64encode(event_tickets.png("DEMO-PASS-" + identifier())).decode("ascii")
    refreshed = '<p role="status">Код обновлён</p>' if request.query("refresh") == "1" else ""
    body = ('<section class="panel dean-campus-pass"><div class="dean-campus-pass-code">'
            f'<img src="data:image/png;base64,{code}" alt="Тестовый QR-код пропуска" width="240" height="240"></div>'
            '<div class="dean-campus-pass-info"><p class="dean-campus-pass-label">Демопропуск</p>'
            f'{appearance.icon("qr")}<h2>Учебный QR-код</h2>'
            '<p class="meta">Код для демонстрации. Через турникет с ним не пройти.</p>'
            f'{link("/campus-pass?refresh=1", "Обновить код", "dean-pass-refresh")}{refreshed}</div></section>')
    return page(request, "Пропуск", body, section="campus-pass", breadcrumb=back("/all-services", "Сервисы"))
