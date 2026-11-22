"""Role-specific first-run tour over existing application pages."""
from urllib.parse import urlencode, urlsplit

from .access_paths import destination
from .domain import timestamp
from .ui import button, e, form, hidden, link

STUDENT = (
    ("/", "#main .rw-lead", "Сегодня", "Посмотри ближайшую пару и задачи на сегодня. Отсюда можно открыть занятие или продолжить работу над задачей."),
    ("/schedule", "#main .rw-calendar-tools", "Расписание", "Выбери день, неделю или месяц. В карточке занятия указана аудитория. Если она есть на карте, можно построить маршрут."),
    ("/grades", "#main h1", "Оценки", "Открой дисциплину, чтобы увидеть оценки и дату ближайшей аттестации."),
    ("/requests", "#my-requests .panel-head", "Заявки", "Подай заявку из каталога услуг. Ответ сотрудника и готовый документ появятся в карточке заявки."),
    ("/all-services", "#main h1", "Сервисы", "Здесь собраны разделы учёбы, библиотека и карта кампуса."),
    ("/bookings", "#main h1", "Бронирования", "Выбери помещение или занятие, посмотри свободное время и оформи запись. Её можно проверить в разделе «Мои бронирования»."),
    ("/lecture-library", "#main h1", "Лекции", "Открой запись, конспект или задания. Материалы преподавателя доступны участникам его группы."),
    ("/notifications", "#notification-feed .panel-head", "Уведомления", "Здесь объявления деканата, изменения расписания и напоминания о сроках. Можно оставить только непрочитанные."),
    ("/assistant", "#assistant-chat .dean-chat-header", "Вишка", "Спроси о расписании или поручай Вишке учебные задачи. Если нужно изменить данные, Вишка сначала покажет, что собирается сделать."),
)
STAFF = (
    ("/staff", "#staff-queue .panel-head", "Очередь заявок", "Выбери обращение своей группы и посмотри, какого действия ждёт студент."),
    ("/staff", "#staff-schedule .panel-head", "Занятия и переносы", "Открой занятие своей группы, чтобы сверить аудиторию и при необходимости оформить перенос."),
    ("/staff/grades", "#main h1", "Учебные результаты", "В ведомости собраны оценки и контрольные точки доступных тебе групп."),
    ("/staff?service=enrolment-certificate", "#staff-queue .panel-head", "Документы", "Здесь показаны заявки на справки. Открой заявку, чтобы подготовить документ или уточнить данные."),
    ("/staff/manage", "#main h1", "Услуги и ресурсы", "Открой каталог услуг и записи на доступные ресурсы."),
    ("/staff/bookings", "#main h1", "Записи студентов", "Проверь окна бронирования и список записавшихся. Студенты видят свободные места в своих кабинетах."),
    ("/staff/announcements", "#staff-announcement-compose .panel-head", "Объявления", "Выбери доступную группу, напиши текст и опубликуй его. Студенты получат объявление в уведомлениях."),
    ("/lecture-library", "#main h1", "Лекции", "Открой видеотеку или записи группы. Для доступных групп можно подготовить материалы к опубликованной записи."),
    ("/assistant", "#assistant-chat .dean-chat-header", "Вишка", "Спроси про расписание или очередь заявок. Ответ учитывает твои группы."),
)


def steps(user):
    return STAFF if user["role"] == "staff" else STUDENT


def home(user):
    return steps(user)[0][0]


def step_number(value, user):
    if len(value) == 1 and value in "123456789" and int(value) <= len(steps(user)):
        return int(value)
    return 0


def step_url(user, number, target):
    path = steps(user)[number - 1][0]
    return path + ("&" if "?" in path else "?") + urlencode({"tour": number, "next": target})


def tour_target(request):
    return destination(request.query("next")) or home(request.user)


def entry(request, db):
    from .web import redirect
    step = step_number(request.query("step"), request.user) or 1
    return redirect(step_url(request.user, step, tour_target(request)))


def position(request, db):
    number = step_number(request.query("tour"), request.user)
    if number:
        return number if request.path == urlsplit(steps(request.user)[number - 1][0]).path else 0
    if request.path != home(request.user) or getattr(request, "access_offer", ""):
        return 0
    return 0 if db.execute("SELECT 1 FROM onboarding WHERE owner_id=?", (request.user["id"],)).fetchone() else 1


def overlay(request):
    number = request.tour_step
    guide = steps(request.user)
    _, anchor, title, description = guide[number - 1]
    target = tour_target(request)
    fields = hidden("next", target)
    previous = link(step_url(request.user, number - 1, target), "Назад", "dean-tour-back") if number > 1 else ""
    onward = (link(step_url(request.user, number + 1, target), "Далее", "rw-primary-link")
              if number < len(guide) else form(request, "/onboarding/finish", fields + button("Завершить")))
    skip = form(request, "/onboarding/skip", fields + '<button class="quiet" data-overlay-dismiss>Пропустить</button>')
    automatic = " data-tour-automatic" if not request.query("tour") else ""
    return (
        f'<dialog open class="dean-tour" data-dean-overlay{automatic} data-tour-anchor="{e(anchor)}" aria-labelledby="tour-title">'
        f'<p class="meta">Шаг {number} из {len(guide)}</p>'
        f'<progress value="{number}" max="{len(guide)}" aria-label="Прогресс знакомства"></progress>'
        f'<h2 id="tour-title" tabindex="-1" autofocus>{e(title)}</h2><p>{e(description)}</p>'
        f'<div class="dean-tour-actions">{skip}<div class="toolbar">{previous}{onward}</div></div></dialog>'
    )


def decide(db, user, decision, target):
    db.execute("INSERT INTO onboarding VALUES (?,?,?) ON CONFLICT(owner_id) DO NOTHING",
               (user["id"], "completed" if decision == "finish" else "skipped", timestamp()))
    return destination(target) or home(user)
