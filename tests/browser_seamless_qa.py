"""Real Chromium smoke checks with a temporary SQLite DB. Run outside a sandbox that blocks loopback."""
import argparse
import json
import os
import re
from contextlib import closing
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Thread
from urllib.parse import urlencode, urlsplit
from wsgiref.simple_server import make_server

from dean import access_state, auth, public_schedule, public_sync
from dean.__main__ import LocalRequestHandler, LocalServer
from dean.access_config import AccessConfig
from dean.config import WebConfig
from dean.db import connect, migrate
from dean.seed import credentials_path, seed
from dean.web import create_app

LOCAL = "localhost"
SOURCE, GROUP, WEEK = "tusur-timetable", "fvs/516-1", "2026-11-16"
PERSONAL = "demo-personal"


def password_login(page, username, password):
    page.get_by_label("Логин", exact=True).fill(username)
    page.get_by_label("Пароль", exact=True).fill(password)
    with page.expect_response(lambda response: response.request.method == "POST" and
                              urlsplit(response.url).path == "/login") as submitted:
        page.get_by_role("button", name="Войти", exact=True).click()
    response = submitted.value
    address = urlsplit(response.url)
    origin_matches = response.request.header_value("origin") == f"{address.scheme}://{address.netloc}"
    assert response.status == 303, f"Вход вернул HTTP {response.status}, совпадение Origin {origin_matches}."


def full_width(page, width):
    widths = page.evaluate("() => [innerWidth, document.documentElement.scrollWidth]")
    assert widths == [width, width], widths


def skip_tour(page):
    page.wait_for_load_state("domcontentloaded")
    action = page.locator("dialog[open].dean-tour [data-overlay-dismiss]")
    if action.count():
        with page.expect_navigation(wait_until="domcontentloaded"):
            action.click()


def paths(browser, origin, passwords, width):
    purpose = "Браузерная проверка для личного архива"
    context = browser.new_context(viewport={"width": width, "height": 800}, has_touch=width == 360)
    with closing(context):
        page = context.new_page()
        page.goto(origin + "/login")
        full_width(page, width)
        assert not page.get_by_role("button", name="Войти через MAX", exact=True).is_visible()
        for caption in ("Логин", "Пароль", "Войти"):
            locator = page.get_by_text(caption, exact=True) if caption in {"Логин", "Пароль"} else page.get_by_role("button", name=caption, exact=True)
            assert locator.is_visible(), caption
            rect = locator.bounding_box()
            assert rect and rect["y"] + rect["height"] < 800, (caption, rect)
        page.get_by_role("link", name="Посмотреть расписание без входа").click()
        group = "/universities/group?" + urlencode({"source": SOURCE, "group": GROUP, "week": WEEK})
        link = page.locator(f'a[href*="group=fvs%2F516-1"]').first
        link.click()
        page.get_by_text("08:50–10:25", exact=False).first.wait_for()
        full_width(page, width)
        page.get_by_role("link", name="Войти в кабинет и вернуться к расписанию").click()
        password_login(page, "student-a", passwords["student-a"])
        page.wait_for_url(origin + group)

        service = "/services/enrolment-certificate"
        page.goto(origin + service)
        page.locator('[name="purpose"]').fill(purpose)
        page.get_by_role("button", name="Отправить заявку", exact=True).click()
        page.wait_for_url(re.compile(re.escape(origin) + r"/requests/[a-f0-9]{32}$"))
        application = page.url
        full_width(page, width)
        page.get_by_role("button", name="Выйти", exact=True).click()
        page.wait_for_url(origin + "/login")
        page.go_back()
        page.wait_for_url(re.compile(re.escape(origin) + r"/login(?:\?|$)"))
        summary = page.get_by_role("article").get_by_text(purpose)
        assert not summary.is_visible()
        page.goto(application)
        password_login(page, "student-a", passwords["student-a"])
        page.wait_for_url(application)
        summary.wait_for()

        page.get_by_role("button", name="Выйти", exact=True).click()
        page.goto(application)
        password_login(page, "student-b", passwords["student-b"])
        page.get_by_role("heading", name="Страница недоступна", exact=True).wait_for()
        assert not summary.is_visible()
        page.get_by_role("link", name="В мой кабинет").click()
        page.wait_for_url(origin + "/")
        skip_tour(page)
        full_width(page, width)
        page.get_by_role("link", name="Профиль", exact=True).click()
        page.get_by_text("Лев Светолесов", exact=True).wait_for()
        page.get_by_role("button", name="Выйти", exact=True).click()
        password_login(page, "staff", passwords["staff"])
        page.wait_for_url(origin + "/staff")
        full_width(page, width)

    with closing(browser.new_context()) as before:
        page = before.new_page()
        page.goto(origin + "/login")
        page.get_by_role("checkbox", name="Запомнить это устройство").check()
        password_login(page, PERSONAL, passwords[PERSONAL])
        page.wait_for_url(origin + "/")
        durable = [cookie for cookie in before.cookies() if cookie["name"] == "dean_remember"]
        assert len(durable) == 1 and durable[0]["expires"] > 0
    with closing(browser.new_context()) as after:
        after.add_cookies(durable)
        page = after.new_page()
        page.goto(origin + "/profile")
        page.wait_for_url(origin + "/profile")
        page.get_by_text("Студент Демо", exact=True).wait_for()
        page.get_by_role("link", name="Активные сеансы и устройства").click()
        page.get_by_role("button", name="Удалить и завершить сеансы").click()
        page.wait_for_url(origin + "/login")
        page.goto(origin + "/profile")
        page.wait_for_url(re.compile(re.escape(origin) + r"/login(?:\?|$)"))


def passkey_path(browser, origin, password):
    with closing(browser.new_context()) as context:
        page = context.new_page()
        session = context.new_cdp_session(page)
        session.send("WebAuthn.enable")
        session.send("WebAuthn.addVirtualAuthenticator", {"options": {
            "protocol": "ctap2",
            "transport": "internal",
            "hasResidentKey": True,
            "hasUserVerification": True,
            "isUserVerified": True,
        }})
        page.goto(origin + "/login")
        password_login(page, PERSONAL, password)
        page.wait_for_url(origin + "/")
        page.goto(origin + "/profile/access")
        page.get_by_role("button", name="Добавить ключ доступа").click()
        page.wait_for_url(origin + "/profile/access?notice=linked")
        page.get_by_role("button", name="Выйти", exact=True).click()
        page.wait_for_url(origin + "/login")

        page.goto(origin + "/profile")
        page.get_by_role("button", name="Ключ доступа", exact=True).click()
        page.wait_for_url(origin + "/profile")
        page.get_by_text("Студент Демо", exact=True).wait_for()

        page.goto(origin + "/profile/access")
        page.get_by_role("button", name="Удалить ключ", exact=True).click()
        page.get_by_role("button", name="Да, удалить", exact=True).click()
        page.wait_for_url(origin + "/login?notice=removed")
        password_login(page, PERSONAL, password)
        page.wait_for_url(origin + "/")


def run_browser(opts, origin, creds):
    from playwright.sync_api import Error, sync_playwright

    failed = False
    with sync_playwright() as playwright:
        launch = {"channel": "chrome"} if opts.chrome else {}
        browser = playwright.chromium.launch(ignore_default_args=["--disable-back-forward-cache"], **launch)
        with closing(browser):
            print(f"Chromium {browser.version}", flush=True)
            cases = [
                (f"{width}px · гостевой вход, ссылка на заявление, общий компьютер и личное устройство",
                 lambda width=width: paths(browser, origin, creds, width))
                for width in (360, 1280)
            ]
            if opts.passkey:
                cases.append(("WebAuthn · виртуальный ключ Chromium",
                              lambda: passkey_path(browser, origin, creds[PERSONAL])))
            for label, run in cases:
                try:
                    run()
                except (AssertionError, Error) as error:
                    failed = True
                    message = str(error)
                    for password in creds.values():
                        message = message.replace(password, "[скрыто]")
                    print(f"FAIL · {label}\n{type(error).__name__} {message}", flush=True)
                else:
                    print(f"PASS · {label}", flush=True)
    return failed


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--chrome", action="store_true", help="Use installed Google Chrome.")
    parser.add_argument("--passkey", action="store_true", help="Verify WebAuthn with Chromium's virtual authenticator.")
    parser.add_argument("--live", action="store_true", help="Keep the temporary server open for interactive QA.")
    opts = parser.parse_args()
    if opts.passkey:
        from importlib.util import find_spec

        if not find_spec("webauthn"):
            parser.error("--passkey requires requirements-auth.txt")
    runtime = Path(".runtime") if opts.live else None
    if runtime:
        runtime.mkdir(mode=0o700, exist_ok=True)
    with TemporaryDirectory(prefix="dean-browser-seamless-", dir=runtime) as temp:
        path = Path(temp) / "qa.sqlite3"
        with closing(connect(path)) as db:
            migrate(db)
            seed(db, path)
            password = auth.token()
            access_state.new_demo_account(db, PERSONAL, password)
            week = public_schedule.week_scope(SOURCE, GROUP, WEEK)
            public_sync.publish(db, public_sync.stage_sample(db, week))
        creds = {**json.loads(credentials_path(path).read_text()), PERSONAL: password}
        app = create_app(path, WebConfig(), access=AccessConfig(passkeys=opts.passkey))
        with make_server("127.0.0.1", 0, app, server_class=LocalServer, handler_class=LocalRequestHandler) as server:
            worker = Thread(target=server.serve_forever, daemon=True)
            worker.start()
            try:
                origin = f"http://{LOCAL}:{server.server_port}"
                if opts.live:
                    live_accounts = path.with_name("browser-accounts.json")
                    fd = os.open(live_accounts, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                    with os.fdopen(fd, "w", encoding="utf-8") as output:
                        json.dump(creds, output)
                    print(f"LIVE_URL {origin}/login", flush=True)
                    print(f"LIVE_ACCOUNTS {live_accounts}", flush=True)
                try:
                    failed = run_browser(opts, origin, creds)
                except Exception as error:
                    if not opts.live:
                        raise
                    failed = True
                    print(f"FAIL · автоматический прогон · {type(error).__name__}. Стенд открыт для проверки в Chrome.",
                          flush=True)
                if opts.live:
                    print("Стенд открыт до Ctrl+C.", flush=True)
                    try:
                        worker.join()
                    except KeyboardInterrupt:
                        pass
            finally:
                server.shutdown()
                worker.join()
    return int(failed)


if __name__ == "__main__":
    raise SystemExit(main())
