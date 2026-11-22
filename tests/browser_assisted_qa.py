"""Exercise the first-run and assisted flows in Chrome using local service fixtures."""
import argparse
from contextlib import closing, contextmanager
from datetime import datetime, timedelta
import json
import os
from pathlib import Path
import re
from tempfile import TemporaryDirectory
from threading import Thread
from time import sleep
from traceback import format_exception
from unittest.mock import patch
from urllib.parse import parse_qs, urlencode, urlsplit
from wsgiref.simple_server import make_server

from playwright.sync_api import expect, sync_playwright

from dean import ai, clock, telegram_worker
from dean.__main__ import LocalServer
from dean.access_config import AccessConfig
from dean.config import WebConfig
from dean.db import connect, migrate
from dean.domain import DomainError
from dean.seed import credentials_path, seed
from dean.web import create_app
from tests.browser_railway_qa import QuietHandler, open_page, palette, screenshot, submit
from tests.browser_seamless_qa import full_width, password_login
from tests.test_assisted_dean import PHOTO, PIXEL
from tests.test_hyper_5 import CHATS, CONFIG, ENV, FakeTransport, message


TIME = datetime.fromisoformat("2026-11-20T08:35:00+00:00")
WEEK = "2026-11-23"
PHOTO_LESSON = "Алгоритмы и структуры данных"
REVIEW_LESSON = "Проектная лаборатория"
MANUAL = "Подготовка к семинару"
PRIVATE = "Личная встреча"
TELEGRAM = "/profile/telegram"
SHARE = "/calendar/share"


class OfflineModel:
    enabled = True
    daily_limit = 100
    total_limit = 300

    def __init__(self):
        self.photo_state = "success"

    def complete(self, messages, max_tokens, *, model):
        sleep(.32)
        if model == ai.CHAT_MODEL:
            return {"action":"draft_certificate", "purpose":"Для учебного проекта"}
        if self.photo_state == "unreadable":
            return {"events":[]}
        if self.photo_state == "error":
            raise DomainError(503, "Распознавание временно недоступно. Попробуй позже.")
        return {"events":[
            {"day":1, "start":"12:00", "end":"13:20", "subject":PHOTO_LESSON,
             "room":"Корпус 3, ауд. 307", "kind":"практика", "uncertain":False},
            {"day":3, "start":"14:10", "end":"15:30", "subject":REVIEW_LESSON,
             "room":"А-201", "kind":"семинар", "uncertain":True},
        ]}


@contextmanager
def fixture():
    with TemporaryDirectory(prefix="dean-v30-ui-") as directory, patch.dict(os.environ, {"DEAN_TELEGRAM_MODE":"off"}):
        path = Path(directory) / "demo.sqlite3"
        with clock.at(TIME), closing(connect(path)) as db:
            migrate(db)
            seed(db, path, anchor=TIME.date())
        passwords = json.loads(credentials_path(path).read_text())
        engine, instant = OfflineModel(), [TIME]
        app = create_app(path, WebConfig(), access=AccessConfig(), ai_engine=engine)

        def serve(env, respond):
            with clock.at(instant[0]):
                return app(env, respond)

        with make_server("127.0.0.1", 0, serve, server_class=LocalServer, handler_class=QuietHandler) as server:
            thread = Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                yield f"http://localhost:{server.server_port}", passwords, path, engine, instant
            finally:
                server.shutdown()
                thread.join()


def capture(page, out, name):
    width = page.viewport_size["width"]
    full_width(page, width)
    if not page.locator(".dean-tour:modal").count():
        page.evaluate("window.scrollTo({top: 0, behavior: 'instant'})")
    screenshot(page, out, f"{name}-{width}")


def login(context, origin, role, passwords, *, next_path="", theme=""):
    page = context.new_page()
    page.goto(origin + ("/login?next=" + next_path if next_path else "/login"))
    if theme:
        palette(page, theme)
    password_login(page, role, passwords[role])
    page.wait_for_url(origin + (next_path or ("/staff" if role == "staff" else "/")))
    return page


def click_key(page, element):
    element.focus()
    expect(element).to_be_focused()
    page.keyboard.press("Enter")


def onboarding(student, other, staff, origin, out):
    journeys = (
        (student, "student", ("Сегодня", "Расписание", "Оценки", "Заявки", "Сервисы", "Бронирования",
                              "Лекции", "Уведомления", "Вишка")),
        (staff, "staff", ("Очередь заявок", "Занятия и переносы", "Учебные результаты",
                          "Документы", "Услуги и ресурсы", "Записи студентов", "Объявления", "Лекции", "Вишка")),
    )
    for page, role, titles in journeys:
        dialog = page.locator("[data-dean-overlay]")
        expect(dialog.locator("h2")).to_have_text(titles[0])
        expect(dialog.locator("h2")).to_be_focused()
        for _ in range(4):
            page.keyboard.press("Tab")
            assert dialog.evaluate("(el) => el.contains(document.activeElement)")
        click_key(page, dialog.get_by_role("link", name="Далее", exact=True))
        expect(dialog.locator("h2")).to_have_text(titles[1])
        click_key(page, dialog.get_by_role("link", name="Назад", exact=True))
        for index, title in enumerate(titles, 1):
            expect(dialog.locator("h2")).to_have_text(title)
            expect(dialog.locator("progress")).to_have_attribute("value", str(index))
            expect(page.locator(".dean-tour-anchor")).to_be_visible()
            assert dialog.evaluate("(el) => el.matches(':modal')")
            if index in {2, 6}:
                capture(page, out, f"onboarding-{role}-{index}")
            if index < len(titles):
                click_key(page, dialog.get_by_role("link", name="Далее", exact=True))
        submit(page, "Завершить", inside=dialog)
        page.reload()
        expect(dialog).to_have_count(0)
        chat = page.locator("#dashboard-assistant")
        content_box = page.locator(".dean-dashboard-content").bounding_box()
        if page.viewport_size["width"] == 1280:
            expect(chat.get_by_label("Сообщение")).to_be_visible()
            chat_box = chat.bounding_box()
            assert chat_box["x"] >= content_box["x"] + content_box["width"]
            if role == "student":
                lead = page.locator(".rw-lead").bounding_box()
                assert lead["width"] > content_box["width"] * .9
        else:
            expect(chat).to_be_hidden()
            expect(page.locator(".dean-assistant-launcher")).to_be_visible()
        capture(page, out, f"dashboard-{role}")

    assert urlsplit(other.url).path == "/schedule"
    target = "/requests"
    other.goto(origin + "/onboarding?" + urlencode({"next": target}))
    dialog = other.locator("[data-dean-overlay]")
    expect(dialog.locator("h2")).to_have_text("Сегодня")
    other.keyboard.press("Escape")
    other.wait_for_url(origin + target)
    expect(dialog).to_have_count(0)

    if student.viewport_size["width"] == 360:
        student.bring_to_front()
        student.locator(".rw-more > summary").click()
        expect(student.locator(".rw-more-sheet").get_by_role("link", name="Открыть Вишку")).to_be_visible()
        click_key(student, student.locator(".rw-more-sheet").get_by_role("link", name="Первые шаги"))
        expect(student.locator("[data-dean-overlay] h2")).to_have_text("Сегодня")
        initial_size = student.viewport_size
        student.set_viewport_size({"width": 360, "height": 560})
        dialog = student.locator("[data-dean-overlay]")
        next_step = dialog.get_by_role("link", name="Далее", exact=True)
        expect(next_step).to_be_in_viewport()
        assert dialog.evaluate("(el) => el.getBoundingClientRect().bottom <= document.querySelector('.rw-dock').getBoundingClientRect().top")
        capture(student, out, "onboarding-short")
        submit(student, "Пропустить", inside=dialog)
        student.set_viewport_size(initial_size)


def upload_photo(page, payload, status=303):
    form = page.locator(".dean-photo-form")
    form.locator('input[name="file"]').set_input_files({"name":"расписание.png", "mimeType":"image/png", "buffer":payload})
    submit(page, "Распознать расписание по фото", inside=form, status=status)


def photo(student, other, origin, width, engine, out):
    engine.enabled = False
    main = open_page(student, origin, PHOTO, width)
    expect(main.get_by_role("link", name="Импортировать CSV")).to_be_visible()
    capture(student, out, "photo-disabled")
    engine.enabled = True
    main = open_page(student, origin, PHOTO, width)
    main.locator('[name="week"]').fill(WEEK)
    capture(student, out, "photo-form")
    cases = (
        ("Некорректный файл".encode(), "success", 422, "Выбери читаемое фото", "bad-file"),
        (PIXEL, "unreadable", 422, "На фото не нашлись пары", "unreadable"),
        (PIXEL, "error", 503, "Распознавание временно недоступно", "api"),
    )
    for image, state, status, copy, label in cases:
        engine.photo_state = state
        upload_photo(student, image, status)
        warning = main.get_by_role("alert")
        expect(warning).to_contain_text(copy)
        expect(warning).to_be_focused()
        expect(main.locator('[name="week"]')).to_have_value(WEEK)
        capture(student, out, "photo-" + label)
    engine.daily_limit = 1
    upload_photo(student, PIXEL, 429)
    expect(main.get_by_role("alert")).to_contain_text("На сегодня лимит")
    capture(student, out, "photo-limit")
    engine.daily_limit = 100
    engine.photo_state = "success"
    student.evaluate("""() => {
        const note = document.querySelector('[data-photo-status]');
        const seen = [];
        new MutationObserver(() => {
            if (note.textContent && !seen.includes(note.textContent)) {
                seen.push(note.textContent);
                sessionStorage.setItem('qa-photo-steps', JSON.stringify(seen));
            }
        }).observe(note, {childList: true, subtree: true});
    }""")
    upload_photo(student, PIXEL)
    draft_path = urlsplit(student.url).path
    assert re.fullmatch(r"/calendar/import/[a-f0-9]{32}", draft_path)
    seen = student.evaluate("JSON.parse(sessionStorage.getItem('qa-photo-steps'))")
    assert seen == ["Готовим фото…", "Распознаём расписание…"], seen
    open_page(other, origin, draft_path, width, status=404)
    cards = main.locator(".dean-photo-row")
    expect(cards).to_have_count(2)
    expect(cards.filter(has_text=PHOTO_LESSON).get_by_text("Добавится событие", exact=True)).to_be_visible()
    uncertain = cards.filter(has_text=REVIEW_LESSON)
    expect(uncertain.get_by_text("Сверь день, название и время со снимком.")).to_be_visible()
    confirm = main.get_by_role("button", name="Подтвердить личный импорт", exact=True)
    expect(confirm).to_have_count(0)
    capture(student, out, "photo-preview")
    submit(student, "Исключить строку", inside=uncertain)
    expect(confirm).to_be_visible()
    submit(student, "Вернуть строку", inside=uncertain)
    expect(confirm).to_have_count(0)
    click_key(student, uncertain.locator("summary"))
    uncertain.locator('[name="room"]').fill("А-202")
    uncertain.get_by_label("Сверил строку").check()
    submit(student, "Сохранить строку", inside=uncertain)
    expect(confirm).to_be_visible()
    submit(student, "Подтвердить личный импорт")
    expect(main.get_by_text("Изменения применены к твоему календарю.")).to_be_visible()
    open_page(student, origin, "/schedule?view=week&date=" + WEEK, width)
    expect(main.get_by_role("link", name=REVIEW_LESSON, exact=True)).to_be_visible()


def ask(page, text):
    chat = page.locator(".dean-assistant-form")
    answer = chat.get_by_label("Сообщение", exact=True)
    answer.fill(text)
    answer.focus()
    page.keyboard.press("Tab")
    expect(chat.get_by_role("button", name="Отправить", exact=True)).to_be_focused()
    page.keyboard.press("Enter")


def chat(student, staff, origin, width, db_path, engine, out):
    engine.enabled = False
    main = open_page(student, origin, "/assistant", width)
    log = main.locator("[data-chat-log]")
    capture(student, out, "assistant-empty")
    main.get_by_role("button", name="Расписание сегодня", exact=True).click()
    expect(log.get_by_role("link", name="Открыть расписание")).to_be_visible()
    capture(student, out, "assistant-reply")
    ask(student, "Открой расписание")
    student.wait_for_url(origin + "/schedule")
    open_page(student, origin, "/assistant", width)
    student.route("**/assistant", lambda request: request.abort(), times=1)
    ask(student, "Мои заявки")
    expect(main.locator("[data-chat-status]")).to_contain_text("Сообщение осталось в поле.")
    expect(main.get_by_label("Сообщение", exact=True)).to_have_value("Мои заявки")
    capture(student, out, "assistant-error")
    main.get_by_role("button", name="Повторить отправку").click()
    expect(log.get_by_role("link", name="Открыть заявки")).to_be_visible()

    for text, caption, result in (
        ("Создай задачу Подготовить стенд", "Создать задачу «Подготовить стенд»", "task"),
        ("Отметь задачу Подготовить стенд выполненной", "Отметить задачу «Подготовить стенд» выполненной", "done"),
        ("Подготовь черновик справки для учебного проекта", "Создать черновик справки об обучении", "draft"),
    ):
        engine.enabled = result == "draft"
        open_page(student, origin, "/assistant", width)
        ask(student, text)
        if result == "draft":
            expect(main.locator("[data-chat-status]")).to_have_text("Помощник готовит ответ…")
        proposal = log.locator(".dean-chat-confirmation").filter(has_text=caption)
        expect(proposal).to_contain_text(caption)
        expect(proposal.get_by_role("button", name="Подтвердить")).to_be_focused()
        if result == "task":
            with closing(connect(db_path)) as db:
                assert not db.execute("SELECT 1 FROM task WHERE title='Подготовить стенд'").fetchone()
        capture(student, out, "assistant-" + result)
        submit(student, "Подтвердить", inside=proposal)
        with closing(connect(db_path)) as db:
            if result == "draft":
                aid = urlsplit(student.url).path.rsplit("/", 1)[-1]
                assert db.execute("SELECT status FROM application WHERE id=?", (aid,)).fetchone()["status"] == "draft"
            else:
                status = db.execute("SELECT state FROM task WHERE title='Подготовить стенд'").fetchone()["state"]
                assert status == ("todo" if result == "task" else "done")
    main = open_page(staff, origin, "/assistant", width)
    main.get_by_role("button", name="Очередь заявок", exact=True).click()
    expect(main.locator("[data-chat-log] a")).to_have_attribute("href", "/staff")


def sharing(student, other, guest, origin, width, out, other_password):
    main = open_page(student, origin, SHARE + "?week=2026-12-07", width)
    expect(main.get_by_role("heading", name="На этой неделе нет личных событий")).to_be_visible()
    capture(student, out, "share-empty")
    for title in (MANUAL, PRIVATE):
        main = open_page(student, origin, "/events/new", width)
        for key, value in (("title", title), ("starts", WEEK + "T09:00"), ("ends", WEEK + "T10:20"), ("location", "А-104")):
            main.locator(f'[name="{key}"]').fill(value)
        submit(student, "Сохранить событие")

    main = open_page(student, origin, "/schedule?view=week&date=" + WEEK, width)
    main.locator(".dean-schedule-actions > summary").click()
    main.get_by_role("link", name="Поделиться неделей").click()
    student.wait_for_url(origin + SHARE + "?week=" + WEEK)
    for title in (PHOTO_LESSON, MANUAL):
        main.get_by_role("checkbox", name=re.compile(re.escape(title))).check()
    expect(main.locator("[data-share-count]")).to_have_text("Выбрано событий · 2")
    capture(student, out, "share-select")
    submit(student, "Создать ссылку", status=201)
    expect(main.get_by_role("heading", name="Ссылка готова")).to_be_visible()
    url = main.get_by_label("Ссылка на расписание").input_value()
    student.context.grant_permissions(["clipboard-read", "clipboard-write"], origin=origin)
    main.get_by_role("button", name="Скопировать ссылку").click()
    expect(main.locator("[data-copy-status]")).to_have_text("Ссылка скопирована.")
    assert student.evaluate("navigator.clipboard.readText()") == url
    student.evaluate("""() => Object.defineProperty(navigator, 'clipboard', {
      configurable: true, value: {writeText: () => Promise.reject(new Error('blocked for QA'))}
    })""")
    main.get_by_role("button", name="Скопировать ссылку").click()
    expect(main.locator("[data-copy-status]")).to_contain_text("Ссылка выделена")
    assert main.locator("#calendar-share-url").evaluate("(el) => el.selectionEnd === el.value.length")
    capture(student, out, "share-link")
    path = urlsplit(url).path
    assert guest.request.get(origin + path).headers["referrer-policy"] == "no-referrer"
    main = open_page(guest, origin, path, width)
    expect(main.get_by_role("link", name="Войти и добавить к себе")).to_be_visible()
    expect(main.get_by_text(PHOTO_LESSON, exact=True)).to_be_visible()
    expect(main.get_by_text(PRIVATE, exact=True)).to_have_count(0)
    capture(guest, out, "share-guest")
    main.get_by_role("link", name="Войти и добавить к себе").click()
    password_login(guest, "student-b", other_password)
    guest.wait_for_url(origin + path)
    with guest.expect_request(lambda request: request.method == "POST" and urlsplit(request.url).path == path + "/copy") as posted:
        submit(guest, "Подготовить импорт к себе")
    assert posted.value.header_value("origin") == origin
    expect(main.get_by_role("heading", name="Предпросмотр импорта")).to_be_visible()
    expect(main.get_by_text(MANUAL, exact=True)).to_be_visible()
    submit(guest, "Подтвердить личный импорт")
    main = open_page(student, origin, SHARE, width)
    submit(student, "Отозвать")
    expect(main.get_by_text("Доступ отозван")).to_be_visible()
    open_page(guest, origin, path, width, status=404)
    main = open_page(other, origin, "/schedule?view=week&date=" + WEEK, width)
    expect(main.get_by_role("link", name=MANUAL, exact=True)).to_be_visible()


def telegram(student, staff, origin, width, db_path, instant, out):
    main = open_page(student, origin, TELEGRAM, width)
    expect(main.get_by_text("Канал Telegram пока недоступен")).to_be_visible()
    capture(student, out, "telegram-disabled")
    transport = FakeTransport()

    def pump(*updates):
        transport.batch = list(updates)
        with clock.at(instant[0]), closing(connect(db_path)) as db:
            telegram_worker.tick(db, CONFIG, transport)

    def begin(page, chat_id, uid):
        main = open_page(page, origin, TELEGRAM, width)
        submit(page, "Подключить Telegram", status=200)
        href = main.get_by_role("link", name="Открыть бота в Telegram").get_attribute("href")
        nonce = parse_qs(urlsplit(href).query)["start"][0]
        if uid == 1:
            capture(page, out, "telegram-start")
        pump(message(uid, chat_id, "/start " + nonce))
        main.get_by_role("link", name="Вернуться к подтверждению").click()
        expect(main.get_by_label("Код из сообщения", exact=True)).to_be_visible()
        return next(re.search(r"Код привязки: ([0-9a-f]{12})", text)[1]
                    for dest, text in reversed(transport.sent) if dest == chat_id and text.startswith("Код привязки:"))

    with patch.dict(os.environ, {**ENV, "DEAN_TELEGRAM_PUBLIC":"0"}):
        proof = begin(student, CHATS[0], 1)
        pin = main.get_by_label("Код из сообщения", exact=True)
        pin.fill("0" * 12 if proof != "0" * 12 else "1" * 12)
        submit(student, "Подтвердить", status=403)
        warning = main.get_by_role("alert")
        expect(warning).to_contain_text("Код не совпал")
        expect(warning).to_be_focused()
        capture(student, out, "telegram-wrong-code")
        instant[0] += timedelta(minutes=11)
        pin.fill(proof)
        submit(student, "Подтвердить", status=409)
        expect(warning).to_contain_text("Срок привязки истёк")
        capture(student, out, "telegram-expired")
        proof = begin(student, CHATS[0], 2)
        pin.fill(proof)
        submit(student, "Подтвердить")
        expect(main.get_by_text("Привязано", exact=True)).to_be_visible()
        pump()
        assert [{"text":"Задачи"},{"text":"Заявки"}] in transport.menus[-1]["keyboard"]
        capture(student, out, "telegram-settings")
        main.get_by_label("Сводка дня после 09:00").check()
        submit(student, "Сохранить настройки")
        expect(main.get_by_label("Сводка дня после 09:00")).to_be_checked()
        staff_main = staff.locator("main")
        proof = begin(staff, CHATS[1], 3)
        staff_main.get_by_label("Код из сообщения", exact=True).fill(proof)
        submit(staff, "Подтвердить")
        pump()
        assert [{"text":"Очередь заявок"},{"text":"Настройки"}] in transport.menus[-1]["keyboard"]
        expect(staff_main.get_by_role("button", name="Сохранить настройки")).to_have_count(0)
        capture(staff, out, "telegram-staff")
        manage = main.locator(".dean-telegram-manage")
        click_key(student, manage.locator("summary"))
        expect(manage.get_by_text("Отключить Telegram?", exact=True)).to_be_visible()
        capture(student, out, "telegram-disconnect")
        manage.get_by_role("link", name="Оставить подключённым").click()
        expect(main.get_by_text("Привязано", exact=True)).to_be_visible()
        click_key(student, manage.locator("summary"))
        submit(student, "Отключить", inside=manage)
        expect(main.get_by_role("button", name="Подключить Telegram")).to_be_visible()


def check(browser, width, out):
    with fixture() as (origin, passwords, path, engine, instant):
        try:
            with (closing(browser.new_context(viewport={"width":width,"height":844},
                                              reduced_motion="reduce", has_touch=width == 360)) as learners,
                  closing(browser.new_context(viewport={"width":width,"height":844}, reduced_motion="reduce")) as second,
                  closing(browser.new_context(viewport={"width":width,"height":844}, reduced_motion="reduce")) as employees,
                  closing(browser.new_context(viewport={"width":width,"height":844}, reduced_motion="reduce")) as visitors):
                theme = "chalk" if width == 360 else ""
                student = login(learners, origin, "student-a", passwords, theme=theme)
                other = login(second, origin, "student-b", passwords, next_path="/schedule", theme=theme)
                staff = login(employees, origin, "staff", passwords, theme=theme)
                assert student.locator("main").evaluate("(el) => getComputedStyle(el).animationName") == "none"
                onboarding(student, other, staff, origin, out)
                photo(student, other, origin, width, engine, out)
                chat(student, staff, origin, width, path, engine, out)
                sharing(student, other, visitors.new_page(), origin, width, out, passwords["student-b"])
                telegram(student, staff, origin, width, path, instant, out)
                if width == 360:
                    expect(student.locator("html")).to_have_attribute("data-theme", "chalk")
        except Exception as error:
            report = "".join(format_exception(error))
            for secret in passwords.values():
                report = report.replace(secret, "[скрыто]")
            raise RuntimeError(report) from None
    print(f"PASS {width} px · 9 шагов для обеих ролей, фото, чат, общая неделя, Telegram, клавиатура и уменьшенное движение", flush=True)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--width", type=int, choices=(360,1280))
    parser.add_argument("--screenshots", type=Path)
    args = parser.parse_args()
    if args.screenshots:
        args.screenshots.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as playwright, closing(playwright.chromium.launch(channel="chrome")) as browser:
        print(f"Chrome {browser.version}", flush=True)
        for width in (args.width,) if args.width else (360,1280):
            check(browser, width, args.screenshots)


if __name__ == "__main__":
    main()
