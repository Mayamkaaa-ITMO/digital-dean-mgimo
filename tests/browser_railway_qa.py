"""Exercise both palettes and real v29 forms in Chrome against a disposable database."""
import argparse
from contextlib import closing, nullcontext
from datetime import datetime, timedelta
import json
import os
from pathlib import Path
import re
from tempfile import TemporaryDirectory
from threading import Thread
from traceback import format_exception
from unittest.mock import patch
from urllib.parse import parse_qs, urlencode, urlsplit
from wsgiref.simple_server import make_server

from playwright.sync_api import expect, sync_playwright

from dean import clock, coursework, public_schedule, public_sync
from dean.__main__ import LocalRequestHandler, LocalServer
from dean.access_config import AccessConfig
from dean.config import WebConfig
from dean.db import connect, migrate
from dean.seed import UNIVERSITY, at, credentials_path, seed, seed_id
from dean.web import create_app
from tests.browser_seamless_qa import GROUP, SOURCE, WEEK, full_width, password_login, skip_tour

TIME = datetime.fromisoformat("2026-11-18T08:35:00+00:00")
DAY = TIME.date().isoformat()
DENSE_DAY = TIME.date() + timedelta(days=2)
LONG_SUBJECT = "Практикум по проектированию распределённых информационных систем и анализу экспериментальных данных"
GROUP_URL = "/universities/group?" + urlencode({"source": SOURCE, "group": GROUP, "week": WEEK})
SERVICE = "/services/enrolment-certificate"
WIDTHS = (360, 1280)

STUDENT_INDEXES = (
    "/study/subjects", "/grades", "/curriculum", "/retakes", "/study/preparation", "/study/timer",
    "/schedule/context", "/schedule/changes", "/schedule/free", "/calendar/import", "/calendar/export",
    "/student-services", "/equipment", "/library", "/bookings", "/bookings/mine", "/activities",
    "/finance", "/finance/budget", "/residence", "/clubs", "/practice", "/portfolio", "/portfolio/resume",
    "/opportunities", "/courses", "/notifications", "/search", "/profile", "/profile/access", "/profile/sessions",
    "/profile/dashboard", "/profile/data", "/profile/links", "/profile/privacy", "/profile/telegram", "/support",
)
STAFF_INDEXES = (
    "/staff/grades", "/staff/bookings", "/staff/retakes", "/staff/manage", "/staff/manage/services/new",
    "/staff/manage/resources/new", "/staff/equipment", "/staff/library", "/staff/finance", "/staff/moderation",
    "/staff/practice", "/staff/achievements", "/staff/support", "/staff/public",
)


class QuietHandler(LocalRequestHandler):
    def log_message(self, format, *args):
        pass


def open_page(page, origin, path, width, status=200):
    response = page.goto(origin + path, wait_until="domcontentloaded")
    assert response.status == status, (path, response.status, status)
    expect(page.locator("main h1")).to_be_visible()
    page.evaluate("document.fonts.ready.then(() => true)")
    full_width(page, width)
    return page.locator("main")


def submit(page, name, *, inside=None, status=303):
    button = (inside or page.locator("main")).get_by_role("button", name=name, exact=True)
    recoverable = button.locator("xpath=ancestor::form").get_attribute("data-application-form") is not None
    navigation = (page.expect_event("framenavigated", predicate=lambda frame: frame == page.main_frame)
                  if status == 303 else nullcontext())
    with navigation:
        with page.expect_response(lambda response: response.request.method == "POST") as posted:
            button.click()
    expected = 200 if recoverable and status == 303 else status
    assert posted.value.status == expected, (name, urlsplit(posted.value.url).path, posted.value.status, expected)
    if recoverable and expected == 200:
        assert posted.value.header_value("content-type").startswith("application/json")
        assert re.fullmatch(r"/requests/[a-f0-9]{32}", urlsplit(page.url).path), page.url
    page.wait_for_load_state("domcontentloaded")


def palette(page, value):
    root = page.locator("html")
    picker = page.locator(".appearance-picker")
    if not picker.evaluate("(element) => element.open"):
        picker.locator("summary").click()
    picker.locator(f'input[value="{value}"]').check()
    expect(root).to_have_attribute("data-interface", "original" if value == "original" else "railway")
    if value != "original":
        expect(root).to_have_attribute("data-theme", "chalk" if value == "blue-chalk" else value)
        if value == "blue-chalk":
            expect(root).to_have_attribute("data-palette", value)
    page.keyboard.press("Escape")
    expect(picker.locator("summary")).to_be_focused()
    assert not picker.evaluate("(element) => element.open")


def screenshot(page, directory, name):
    if directory:
        page.evaluate("document.fonts.ready.then(() => true)")
        page.evaluate("""() => Promise.all([...document.images]
            .filter(image => image.getBoundingClientRect().top < innerHeight && image.getClientRects().length)
            .map(image => image.decode()))""")
        page.screenshot(path=str(directory / f"{name}.png"), animations="disabled")


def navigation(page, path):
    target = f'a[href="{path}"], a[href^="{path}?"]'
    page.locator('nav[aria-label="Основные разделы"]').locator(target).filter(visible=True).first.click()


def moved_lesson(student, staff, origin, width):
    lesson = "/schedule/" + seed_id("a1")
    main = open_page(staff, origin, "/staff" + lesson, width)
    main.locator('[name="starts"]').fill(f"{DAY}T11:00")
    main.locator('[name="ends"]').fill(f"{DAY}T12:30")
    main.locator('[name="room"]').fill("ДЕМО · А-501")
    submit(staff, "Сохранить изменение")
    main = open_page(student, origin, "/notifications?kind=schedule", width)
    main.locator(f'a[href="{lesson}"]').first.click()
    expect(main.get_by_text("ДЕМО · А-501", exact=False).first).to_be_visible()
    expect(main.get_by_text("Занятие изменено.", exact=True)).to_be_visible()


def certificate(browser, origin, width, credentials, out):
    with closing(browser.new_context(viewport={"width": width, "height": 800}, has_touch=width == 360)) as owner, \
         closing(browser.new_context(viewport={"width": width, "height": 800})) as employee, \
         closing(browser.new_context(viewport={"width": width, "height": 800})) as stranger:
        student, staff, other = owner.new_page(), employee.new_page(), stranger.new_page()
        student.clock.set_fixed_time(TIME)
        browser_errors = []
        for page in (student, staff, other):
            page.on("pageerror", lambda error: browser_errors.append(str(error)))
        suffix = str(width)

        main = open_page(student, origin, "/guest", width)
        student.keyboard.press("Tab")
        expect(student.locator(".skip")).to_be_focused()
        main.get_by_role("link", name="Открыть каталог вузов").click()
        main.locator('input[name="q"]').fill("у__999999")
        main.get_by_role("button", name="Найти", exact=True).click()
        expect(main.get_by_text("Ничего не нашлось", exact=False)).to_be_visible()
        main.locator('input[name="q"]').fill("516-1")
        main.get_by_role("button", name="Найти", exact=True).click()
        main.locator('a[href*="group=fvs%2F516-1"]').first.click()
        expect(main.get_by_text("08:50–10:25", exact=False).first).to_be_visible()
        full_width(student, width)
        screenshot(student, out, f"university-{suffix}")
        main.get_by_role("link", name="Войти в кабинет и вернуться к расписанию").click()
        screenshot(student, out if width == 1280 else None, "login-1280")
        password_login(student, "student-a", credentials["student-a"])
        student.wait_for_url(origin + GROUP_URL)
        student.emulate_media(reduced_motion="reduce")
        navigation(student, "/")
        skip_tour(student)
        expect(main.locator(".rw-lead")).to_be_visible()
        student.evaluate("document.fonts.ready.then(() => true)")
        lead = main.locator(".rw-lead")
        geometry = "(element) => [element.offsetWidth, element.offsetHeight, element.offsetLeft, element.offsetTop]"
        palette(student, "graphite")
        graphite_layout = lead.evaluate(geometry)
        graphite_fill = lead.evaluate("(element) => getComputedStyle(element).getPropertyValue('--lead').trim()")
        screenshot(student, out, f"today-graphite-{suffix}")
        palette(student, "chalk")
        assert lead.evaluate(geometry) == graphite_layout, (graphite_layout, lead.evaluate(geometry))
        assert lead.evaluate("(element) => getComputedStyle(element).getPropertyValue('--lead').trim()") != graphite_fill
        main.locator("h1").click()
        screenshot(student, out, f"today-chalk-{suffix}")

        main.locator(".rw-date-strip [aria-current=date]").click()
        assert parse_qs(urlsplit(student.url).query)["date"] == [DAY]
        expect(student.locator("html")).to_have_attribute("data-theme", "chalk")
        main.get_by_text("Действия", exact=True).click()
        main.get_by_role("link", name="Вид Raspay").click()
        expect(student.locator("html")).to_have_attribute("data-theme", "chalk")
        full_width(student, width)
        screenshot(student, out, f"raspay-chalk-{suffix}")
        student.evaluate("localStorage.setItem('dean-appearance', 'original')")
        student.reload()
        expect(student.locator(".rp-wrap")).to_be_visible()
        main.get_by_role("link", name="Исходный интерфейс", exact=True).click()
        expect(student.locator("html")).to_have_attribute("data-interface", "original")
        expect(student.locator(".nav-wrap")).to_be_visible()
        expect(main.locator('form[action="/schedule/filters"]')).to_be_visible()
        full_width(student, width)
        open_page(student, origin, "/", width)
        screenshot(student, out if width == 1280 else None, "today-original-1280")
        palette(student, "chalk")
        navigation(student, "/all-services")
        full_width(student, width)
        screenshot(student, out, f"services-chalk-{suffix}")
        main.locator('.rw-service-tiles a[href="/requests"]').click()
        main.get_by_role("link", name="Подать заявку", exact=True).click()
        main.locator(f'a[href="{SERVICE}"]').click()
        main.locator('[name="purpose"]').fill("   ")
        submit(student, "Отправить заявку", status=422)
        expect(main.get_by_role("alert")).to_be_visible()
        screenshot(student, out if width == 360 else None, "application-validation-360")
        main.locator('[name="purpose"]').fill("Для учебного архива")
        submit(student, "Отправить заявку")
        application = urlsplit(student.url).path
        assert application.startswith("/requests/"), application
        expect(main.locator("article")).to_contain_text("Отправлена")

        open_page(staff, origin, "/login", width)
        password_login(staff, "staff", credentials["staff"])
        staff.wait_for_url(origin + "/staff")
        skip_tour(staff)
        queue = staff.locator('form[action="/staff"][method="get"]')
        queue.locator('[name="status"]').select_option("submitted")
        queue.get_by_role("button", name="Показать").click()
        staff.locator(f'main a[href="{application}"]').click()
        submit(staff, "Взять в рассмотрение")
        return_form = staff.locator('main form[action$="/needs_changes"]')
        return_form.locator("textarea").fill("Добавь название учебного архива.")
        submit(staff, "Вернуть на исправление", inside=return_form)

        open_page(student, origin, "/", width)
        expect(main.locator(".rw-urgent")).to_contain_text("Добавь название учебного архива.")
        screenshot(student, out, f"today-urgent-{suffix}")
        main.get_by_role("link", name="Исправить", exact=True).click()
        main.get_by_role("link", name="Исправить заявление").click()
        main.locator('#request-data [name="purpose"]').fill("Для архива учебного проекта")
        submit(student, "Отправить повторно")
        assert urlsplit(student.url).path == application
        expect(main.locator("article")).to_contain_text("Повторно отправлена")

        open_page(staff, origin, application, width)
        submit(staff, "Взять в рассмотрение")
        submit(staff, "Выдать PDF")
        expect(staff.locator("main article")).to_contain_text("Документ готов")

        open_page(student, origin, "/notifications?kind=application", width)
        prior = main.locator(".notices li").filter(has_text="Ранее · Заявка: требуется исправление")
        expect(prior.get_by_text("Сейчас: Документ готов", exact=True)).to_be_visible()
        expect(prior.locator(f'a[href="{application}#request-history"]')).to_contain_text(
            "№ " + application.rsplit("/", 1)[1][:8])
        main.locator(f'a[href="{application}"]').first.click()
        preview = main.get_by_role("link", name="Открыть PDF", exact=True)
        preview_path = preview.get_attribute("href")
        preview.click()
        student.wait_for_url(origin + preview_path)
        expect(main.locator(".dean-document-text")).to_contain_text("Для архива учебного проекта")
        pdf = main.get_by_role("link", name="Скачать PDF", exact=True).get_attribute("href")
        payload = owner.request.get(origin + pdf)
        assert payload.status == 200 and payload.body().startswith(b"%PDF")
        assert payload.headers["content-disposition"].startswith("attachment;")
        open_page(other, origin, "/login", width)
        password_login(other, "student-b", credentials["student-b"])
        open_page(other, origin, application, width, status=404)
        open_page(other, origin, preview_path, width, status=404)
        open_page(other, origin, urlsplit(pdf).path, width, status=404)
        open_page(other, origin, "/staff/public", width, status=403)
        open_page(student, origin, "/documents", width)
        expect(main.get_by_role("link", name="Открыть PDF", exact=True)).to_be_visible()
        open_page(student, origin, SERVICE, width).locator('[name="purpose"]').fill("Для заявки на конкурс")
        submit(student, "Отправить заявку")
        another = urlsplit(student.url).path
        assert another != application
        open_page(student, origin, "/requests", width)
        for target, purpose in ((application, "Для архива учебного проекта"), (another, "Для заявки на конкурс")):
            item = main.locator(".rows li").filter(has_text=purpose)
            expect(item.locator(f'a[href="{target}"]')).to_contain_text("№ " + target.rsplit("/", 1)[1][:8])
        screenshot(staff, out, f"issued-{suffix}")
        print(f"PASS {width} · гость → вход → календарь → заявка → возврат → повтор → уведомление → PDF; номера, цели и изоляция", flush=True)

        moved_lesson(student, staff, origin, width)
        expanded(student, staff, origin, width, out)
        assert not browser_errors, browser_errors


def expanded(page, staff, origin, width, out):
    main = page.locator("main")
    if width == 360:
        page.locator(".rw-more > summary").click()
        more = page.locator(".rw-more-sheet")
        expect(more.get_by_role("link", name="Задачи", exact=True)).to_be_visible()
        expect(more.get_by_role("link", name="Поиск", exact=True)).to_be_visible()
        expect(more.get_by_role("link")).to_have_text(
            ["ИИ-помощник", "Первые шаги", "Задачи", "Поиск", "Уведомления", "Профиль", "Поддержка"])
        screenshot(page, out, "more-360")
        more.get_by_role("link", name="Поиск", exact=True).focus()
        page.keyboard.press("Escape")
        expect(page.locator(".rw-more > summary")).to_be_focused()
        assert not more.is_visible()
    page.emulate_media(reduced_motion="reduce")
    open_page(page, origin, f"/schedule?view=day&date={DAY}", width)
    assert main.locator(".rw-calendar-days").evaluate("(element) => getComputedStyle(element).animationName") == "none"
    for label in ("Неделя", "Месяц", "Список", "День"):
        main.get_by_role("navigation", name="Период расписания").get_by_role("link", name=label, exact=True).click()
        full_width(page, width)
    expect(main.locator(".rw-calendar-days")).to_have_attribute("data-view", "day")
    main.get_by_text("Действия", exact=True).click()
    main.locator('form[action="/schedule/filters"] [name="kind"]').select_option("lesson")
    submit(page, "Сохранить фильтры")
    expect(main.locator('form[action="/schedule/filters"] [name="kind"]')).to_have_value("lesson")
    screenshot(page, out, f"schedule-chalk-{width}")
    open_page(page, origin, f"/schedule?view=day&date={DENSE_DAY}", width)
    expect(main.locator('.rw-calendar-days [data-event="lesson"]')).to_have_count(6)
    expect(main.get_by_role("link", name=LONG_SUBJECT, exact=True).first).to_be_visible()
    screenshot(page, out, f"dense-{width}")
    open_page(page, origin, "/schedule?view=day&date=2026-11-29", width)
    expect(main.get_by_text("По выбранным фильтрам событий нет.", exact=True)).to_be_visible()
    screenshot(page, out, f"empty-{width}")

    lesson = seed_id("a1")
    open_page(staff, origin, f"/staff/schedule/{lesson}", width)
    staff.locator('main [name="room"]').fill("ДЕМО · А-310")
    submit(staff, "Сохранить изменение")
    open_page(page, origin, "/", width)
    expect(main.locator(".rw-lead")).to_contain_text("Занятие изменено")
    expect(main.locator(".rw-lead")).to_contain_text("А-310")

    open_page(page, origin, "/study/subjects", width)
    main.locator('form[action="/study/subjects"] [name="title"]').fill("Заметки по семинарам")
    submit(page, "Добавить")
    expect(main.get_by_role("heading", name="Заметки по семинарам", exact=True)).to_be_visible()
    teaching(page, staff, origin, width)
    open_page(page, origin, "/tasks/new", width)
    main.locator('[name="title"]').fill("Подготовить конспект к семинару")
    main.locator('[name="due"]').fill(f"{DAY}T18:00")
    submit(page, "Сохранить задачу")
    expect(main.get_by_role("heading", name="Подготовить конспект к семинару", exact=True)).to_be_visible()
    open_page(page, origin, "/", width)
    expect(main.locator(".rw-priority").get_by_text("Подготовить конспект к семинару", exact=True)).to_be_visible()

    open_page(page, origin, "/calendar/import", width)
    csv = main.locator('form[action="/calendar/import/csv"]').filter(has=page.locator("textarea"))
    csv.locator("textarea").fill("key,subject,kind,start,end,room,status\nqa,Разбор конспекта,event,"
                                 "2026-11-20T18:00,2026-11-20T19:00,Кабинет,active")
    submit(page, "Проверить текст", inside=csv)
    expect(main.get_by_role("heading", name="Предпросмотр импорта", exact=True)).to_be_visible()
    submit(page, "Отменить черновик")
    expect(main.get_by_text("Отменён · строк", exact=False)).to_be_visible()

    open_page(page, origin, "/finance", width)
    privacy = main.locator('form[action="/finance/visibility"] [name="private"]')
    privacy.check()
    submit(page, "Сохранить настройку")
    expect(privacy).to_be_checked()
    open_page(page, origin, "/bookings", width)
    main.get_by_role("link", name="Помещения", exact=True).click()
    main.locator('a[href^="/bookables/"]').first.click()
    full_width(page, width)
    if main.locator('a[href^="/slots/"]').count():
        main.locator('a[href^="/slots/"]').first.click()
        full_width(page, width)
    campus_ticket(page, staff, origin, width)

    open_page(page, origin, "/teams", width)
    main.locator('[name="title"]').fill("Учебная группа по расписанию")
    submit(page, "Создать команду")
    expect(main.get_by_role("heading", name="Учебная группа по расписанию", exact=True)).to_be_visible()
    open_page(page, origin, "/profile/privacy", width)
    control = main.locator('[name="hide"]')
    control.check()
    submit(page, "Сохранить")
    expect(control).to_be_checked()
    open_page(page, origin, "/support", width)
    main.locator('[name="title"]').fill("Вопрос об учебном расписании")
    main.locator('[name="body"]').fill("Проверочное обращение для демонстрационной группы.")
    submit(page, "Создать")
    expect(main.get_by_role("heading", name="Вопрос об учебном расписании", exact=True)).to_be_visible()

    staff_main = open_page(staff, origin, "/staff/public", width)
    staff_main.locator('[name="scope"]').select_option(f"{SOURCE}|{GROUP}|{WEEK}")
    submit(staff, "Сравнить")
    expect(staff_main.get_by_role("heading", name="Предпросмотр недели", exact=True)).to_be_visible()
    full_width(staff, width)
    screenshot(staff, out if width == 1280 else None, "operator-preview-1280")
    for target in STUDENT_INDEXES:
        open_page(page, origin, target, width)
    for target in STAFF_INDEXES:
        open_page(staff, origin, target, width)
    public_context(page, origin, width)
    open_page(staff, origin, "/staff", width)
    screenshot(staff, out if width == 1280 else None, "staff-queue-1280")
    print(f"PASS {width} · формы учёбы, календаря, личной работы, кампуса, финансов, команд, профиля и оператора", flush=True)
    print(f"PASS {width} · {len(STUDENT_INDEXES)} разделов студента, {len(STAFF_INDEXES)} разделов сотрудника/оператора, без горизонтального скролла", flush=True)


def campus_ticket(page, staff, origin, width):
    main = open_page(staff, origin, "/staff/manage/resources/new", width)
    title = "Встреча учебной группы"
    main.locator('[name="kind"]').select_option("activity")
    for field, value in (("title", title), ("place", "ДЕМО · А-310"), ("category", "Учебная встреча"),
                         ("starts", f"{DAY}T11:55"), ("ends", f"{DAY}T12:45")):
        main.locator(f'[name="{field}"]').fill(value)
    submit(staff, "Создать")
    submit(staff, "Опубликовать")
    slot = main.locator('a[href^="/staff/slots/"]').get_attribute("href")
    student_main = open_page(page, origin, "/activities", width)
    student_main.get_by_role("link", name=title, exact=True).click()
    student_main.locator(f'a[href="{slot.removeprefix("/staff")}"]').click()
    student_main.get_by_label("Подтверждаю запись при совпадении с парой").check()
    submit(page, "Подтвердить запись")
    submit(page, "Получить билет", status=200)
    expect(student_main.get_by_role("img", name="QR-код билета")).to_be_visible()
    code = student_main.locator('[name="ticket"]').input_value()
    open_page(staff, origin, slot + "/tickets", width)
    main.locator('[name="code"]').fill(code)
    submit(staff, "Проверить код")
    submit(staff, "Подтвердить посещение")
    expect(main.get_by_text("Посещение уже отмечено.", exact=True)).to_be_visible()


def public_context(page, origin, width):
    main = open_page(page, origin, "/schedule/context", width)
    main.locator('[name="selection"]').select_option(f"{SOURCE}|{GROUP}")
    submit(page, "Открыть расписание")
    main = open_page(page, origin, "/", width)
    expect(main.locator(".rw-lead")).to_be_visible()
    open_page(page, origin, f"/schedule?view=week&date={WEEK}", width)
    expect(main.get_by_text("12-я неделя · чётная", exact=True)).to_be_visible()
    week = public_schedule.week_scope(SOURCE, GROUP, WEEK)
    event = public_schedule.lesson_id(week, "687069")
    main.locator(f'a[href^="/schedule/public/{event}?"]').click()
    main.get_by_role("link", name="ф 121", exact=True).click()
    expect(main.get_by_text("Томск, ул. Вершинина, 74", exact=True)).to_be_visible()
    page.go_back()
    main.get_by_label("Моя заметка").fill("Моя заметка о времени следующей пары")
    submit(page, "Сохранить")
    expect(main.get_by_text("Личная запись сохранена.", exact=True)).to_be_visible()
    for source in ("nspu-reference", "guap-reference"):
        group = next(iter(public_schedule.SOURCES[source]["groups"]))
        query = urlencode({"source": source, "group": group})
        open_page(page, origin, f"/universities/group?{query}", width)
        expect(main.get_by_role("heading", name="Справочная сетка группы", exact=True)).to_be_visible()


def teaching(page, staff, origin, width):
    main = open_page(staff, origin, "/staff/work/new", width)
    title = "Подробный разбор расписания семинара и подготовка учебного проекта по анализу данных"
    for field, value in (("title", title), ("description", "Объясни результат."), ("due", "2026-11-19T17:00"),
                         ("rubric", "Обоснование | 10")):
        main.locator(f'[name="{field}"]').fill(value)
    main.locator('[name="response_mode"]').select_option("text")
    submit(staff, "Сохранить условия")
    outline = urlsplit(staff.url).path
    submit(staff, "Опубликовать группе")
    student_main = open_page(page, origin, "/work", width)
    work_link = student_main.locator(".dean-linked-row > a").filter(has_text=title)
    if width <= 390:
        lines = work_link.locator("strong").evaluate("""title => {
            const range = document.createRange();
            range.selectNodeContents(title);
            return range.getClientRects().length;
        }""")
        assert lines >= 2
    work_link.click()
    student_main.locator('[name="answer"]').fill("Первый ответ на учебную работу.")
    submit(page, "Отправить")
    detail = urlsplit(page.url).path
    open_page(staff, origin, outline, width)
    main.locator('a[href^="/staff/work/review/"]').first.click()
    review = urlsplit(staff.url).path
    main.locator('[name="feedback"]').fill("Добавь вывод к ответу.")
    submit(staff, "Вернуть на доработку")
    open_page(page, origin, detail, width)
    expect(student_main.get_by_text("Добавь вывод к ответу.", exact=True)).to_be_visible()
    student_main.locator('[name="answer"]').fill("Доработанный ответ с выводом.")
    submit(page, "Отправить")
    open_page(staff, origin, review, width)
    main.locator('[name="points_0"]').fill("10")
    main.locator('[name="feedback"]').fill("Вывод добавлен.")
    submit(staff, "Опубликовать результат")
    open_page(page, origin, detail, width)
    expect(student_main.get_by_text("Опубликованный результат: 10 баллов.", exact=True)).to_be_visible()


def no_javascript(browser, origin, credentials):
    with closing(browser.new_context(java_script_enabled=False, viewport={"width": 360, "height": 800})) as context:
        page = context.new_page()
        page.goto(origin + "/login")
        password_login(page, "student-a", credentials["student-a"])
        expect(page.locator("main h1")).to_have_text("Сегодня")
        skip_tour(page)
        page.locator(".rw-more > summary").click()
        page.locator(".rw-more-sheet").get_by_role("link", name="Задачи", exact=True).click()
        page.locator("main").get_by_role("link", name="Создать задачу", exact=True).click()
        page.locator('main [name="title"]').fill("Задача без JavaScript")
        submit(page, "Сохранить задачу")
        expect(page.locator("main h1")).to_have_text("Задача без JavaScript")
    print("PASS 360 · без JavaScript: вход, нижнее меню, форма и переход на сохранённую задачу", flush=True)


def seed_dense_day(db):
    with db:
        db.execute("INSERT INTO subject VALUES (?, 'railway-dense', ?)", (UNIVERSITY, LONG_SUBJECT))
        for index, hour in enumerate(range(8, 20, 2)):
            reference = f"railway-dense-{index}"
            db.execute("""
                INSERT INTO schedule_event
                    (id, university_id, group_id, period_id, subject_id, starts_at, ends_at,
                     timezone, kind, room, teacher, source_id, source_event_id, origin)
                SELECT ?, university_id, group_id, period_id, 'railway-dense', ?, ?, timezone, kind,
                       room, teacher, source_id, ?, origin
                FROM schedule_event WHERE id=?
            """, (seed_id(reference), at(DENSE_DAY, hour), at(DENSE_DAY, hour + 1, 30), reference, seed_id("a1")))


def run(browser, width, out):
    with TemporaryDirectory(prefix="dean-railway-qa-") as directory:
        path = Path(directory) / "railway.sqlite3"
        with patch.dict(os.environ, {"DEAN_DB": str(path)}):
            with clock.at(TIME), closing(connect(path)) as db:
                migrate(db)
                seed(db, path, anchor=TIME.date())
                seed_dense_day(db)
                coursework.grant_demo(db, "staff", "demo-ai", "algorithms")
                public_sync.allow_operator(db, "staff")
                week = public_schedule.week_scope(SOURCE, GROUP, WEEK)
                public_sync.publish(db, public_sync.stage_sample(db, week))
            credentials = json.loads(credentials_path(path).read_text())
            app = create_app(path, WebConfig(), access=AccessConfig())
            server_errors = []

            def application(env, start_response):
                def record_status(status, headers, exc_info=None):
                    if status.startswith("5"):
                        server_errors.append((env["REQUEST_METHOD"], env.get("PATH_INFO"), status))
                    return start_response(status, headers, exc_info)

                with clock.at(TIME):
                    return app(env, record_status)

            with make_server("127.0.0.1", 0, application, server_class=LocalServer, handler_class=QuietHandler) as server:
                thread = Thread(target=server.serve_forever, daemon=True)
                thread.start()
                try:
                    origin = f"http://localhost:{server.server_port}"
                    try:
                        certificate(browser, origin, width, credentials, out)
                        if width == 360:
                            no_javascript(browser, origin, credentials)
                    except Exception as error:
                        detail = "".join(format_exception(error))
                        for password in credentials.values():
                            detail = detail.replace(password, "[скрыто]")
                        raise RuntimeError(detail) from None
                finally:
                    server.shutdown()
                    thread.join()
            assert not server_errors, server_errors


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--width", choices=WIDTHS, type=int)
    parser.add_argument("--screenshots", type=Path)
    options = parser.parse_args()
    if options.screenshots:
        options.screenshots.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as playwright, closing(playwright.chromium.launch(channel="chrome")) as browser:
        print(f"Chromium {browser.version}", flush=True)
        for width in (options.width,) if options.width else WIDTHS:
            run(browser, width, options.screenshots)


if __name__ == "__main__":
    main()
