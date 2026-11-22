"""Optional interactive browser regression with a disposable SQLite database."""
from contextlib import closing
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Thread
from wsgiref.simple_server import make_server
import json

from dean import public_schedule, public_sync
from dean.__main__ import LocalServer
from dean.config import WebConfig
from dean.db import connect, migrate
from dean.seed import credentials_path, seed
from dean.web import create_app

SOURCE = "tusur-timetable"
GROUP = "fvs/516-1"
WEEK = "2026-11-16"
BROWSER_WIDTHS = (1280, 360)


def login(page, base, username, password):
    page.goto(base + "/login")
    page.get_by_label("Логин").fill(username)
    page.get_by_label("Пароль").fill(password)
    page.get_by_role("button", name="Войти", exact=True).click()
    page.wait_for_url(base + ("/staff" if username == "staff" else "/"))


def choose_group(page, base):
    page.goto(base + "/schedule")
    page.get_by_role("link", name="Вуз и группа").click()
    page.get_by_label("Вуз и группа").select_option(SOURCE + "|" + GROUP)
    page.get_by_role("button", name="Открыть расписание").click()
    page.wait_for_url(base + "/schedule")
    page.get_by_role("link", name="16 ноября", exact=True).click()
    page.get_by_text("12-я неделя · чётная", exact=True).wait_for()


def layout(page, width):
    dimensions = page.evaluate("() => [innerWidth, document.documentElement.scrollWidth]")
    assert dimensions == [width, width], dimensions


def browser_flow(browser, base, passwords, db_path, width):
    week = public_schedule.week_scope(SOURCE, GROUP, WEEK)
    lesson = "/schedule/public/" + public_schedule.lesson_id(week, "687069")
    note = f"Браузерная проверка при ширине {width}"
    size = {"width": width, "height": 800}
    with_a = browser.new_context(viewport=size)
    with_b = browser.new_context(viewport=size)
    with_staff = browser.new_context(viewport=size)
    try:
        a = with_a.new_page()
        login(a, base, "student-a", passwords["student-a"])
        choose_group(a, base)
        layout(a, width)
        a.locator(f'a[href="{lesson}"]').click()
        a.get_by_role("heading", name="Математика").wait_for()
        layout(a, width)
        a.get_by_role("link", name="ф 121", exact=True).click()
        a.get_by_text("Томск, ул. Вершинина, 74", exact=True).wait_for()
        layout(a, width)
        a.go_back()
        a.get_by_role("link", name="Предмет в расписании").click()
        a.get_by_role("heading", name="Поиск").wait_for()
        a.go_back()
        a.get_by_label("Моя заметка").fill(note)
        a.get_by_label("Напомнить за 30 минут").check()
        a.get_by_role("button", name="Сохранить").click()
        a.get_by_text("Личная запись сохранена.", exact=True).wait_for()
        layout(a, width)
        with closing(connect(db_path)) as db:
            preview = public_sync.stage_sample(db, week)
            assert not any(public_sync.publish(db, preview).values())
        a.reload()
        assert a.get_by_label("Моя заметка").input_value() == note
        b = with_b.new_page()
        login(b, base, "student-b", passwords["student-b"])
        choose_group(b, base)
        b.locator(f'a[href="{lesson}"]').click()
        assert b.get_by_label("Моя заметка").input_value() == ""
        layout(b, width)
        staff = with_staff.new_page()
        login(staff, base, "staff", passwords["staff"])
        assert staff.goto(base + "/staff/public").status == 403
        with closing(connect(db_path)) as db:
            public_sync.allow_operator(db, "staff")
        staff.reload()
        staff.get_by_role("heading", name="Открытые источники").wait_for()
        layout(staff, width)
    finally:
        with_a.close()
        with_b.close()
        with_staff.close()


def main():
    with TemporaryDirectory(prefix="dean-browser-qa-") as work:
        path = Path(work) / "dean.sqlite3"
        with closing(connect(path)) as db:
            migrate(db)
            seed(db, path)
            for date in (WEEK, "2026-11-23"):
                week = public_schedule.week_scope(SOURCE, GROUP, date)
                public_sync.publish(db, public_sync.stage_sample(db, week))
        passwords = json.loads(credentials_path(path).read_text())
        try:
            server = make_server("127.0.0.1", 0, create_app(path, WebConfig()), server_class=LocalServer)
        except OSError as error:
            raise SystemExit(f"Браузерный тест требует доступного loopback. {error.strerror}") from error
        with server:
            try:
                from playwright.sync_api import sync_playwright
            except ImportError as error:
                raise SystemExit("Установи Playwright по инструкции UNIVERSITY_DEMO_ROUTE.md.") from error
            thread = Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                with sync_playwright() as playwright:
                    browser = playwright.chromium.launch()
                    base = f"http://127.0.0.1:{server.server_port}"
                    for width in BROWSER_WIDTHS:
                        if width != BROWSER_WIDTHS[0]:
                            with closing(connect(path)) as db:
                                db.execute("DELETE FROM public_operator")
                        browser_flow(browser, base, passwords, path, width)
                        print(f"OK · Chromium {width} × 800")
            finally:
                server.shutdown()
                thread.join()


if __name__ == "__main__":
    main()
