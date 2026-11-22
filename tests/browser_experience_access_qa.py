"""Check the interface and account continuity on a local HTTPS server with OAuth fixtures."""
import argparse
from contextlib import closing, contextmanager
from pathlib import Path
import re
import ssl
from subprocess import DEVNULL, run
from tempfile import TemporaryDirectory
from threading import Thread
from unittest.mock import patch
from urllib.parse import parse_qs, urlencode, urlsplit
from wsgiref.simple_server import make_server

from playwright.sync_api import expect, sync_playwright

from dean import access_state, auth
from dean.__main__ import LocalServer
from dean.access_config import AccessConfig
from dean.config import WebConfig
from dean.db import connect, migrate
from dean.seed import seed
from dean.web import create_app
from tests.browser_railway_qa import QuietHandler, palette, screenshot, submit
from tests.browser_seamless_qa import full_width, password_login


ACCOUNT = "demo-interface"
PURPOSE = "Для проверки одного кабинета"
SERVICE = "/services/enrolment-certificate"


@contextmanager
def https_fixture():
    with TemporaryDirectory(prefix="dean-interface-https-") as directory:
        root = Path(directory)
        private_key, certificate = root / "key.pem", root / "cert.pem"
        run(["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "1",
             "-subj", "/CN=localhost", "-keyout", str(private_key), "-out", str(certificate)],
            check=True, stdout=DEVNULL, stderr=DEVNULL)
        database = root / "demo.sqlite3"
        password = auth.token()
        with closing(connect(database)) as db:
            migrate(db)
            seed(db, database)
            access_state.new_demo_account(db, ACCOUNT, password)

        selected = [0]

        def service(environ, respond):
            environ["wsgi.url_scheme"] = "https"
            return applications[selected[0]](environ, respond)

        with make_server("127.0.0.1", 0, service, server_class=LocalServer, handler_class=QuietHandler) as server:
            tls = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            tls.load_cert_chain(certificate, private_key)
            server.socket = tls.wrap_socket(server.socket, server_side=True)
            origin = f"https://localhost:{server.server_port}"
            config = WebConfig(origin)
            yandex = {"yandex_client": "browser-demo", "yandex_secret": auth.token()}
            methods = (AccessConfig(), AccessConfig(**yandex), AccessConfig(vk_client="101", **yandex))
            applications = tuple(create_app(database, config, access=method) for method in methods)

            def provider(uri, fields=None, authorization=None):
                address = urlsplit(uri)
                if address.path == "/oauth2/auth":
                    return {"state": fields["state"], "access_token": "demo-vk", "user_id": 101}
                if address.path == "/oauth2/user_info":
                    return {"user": {"user_id": 101}}
                if address.path == "/token":
                    return {"access_token": "demo-yandex"}
                assert address.netloc == "login.yandex.ru"
                return {"client_id": yandex["yandex_client"], "id": 202}

            thread = Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                with patch("dean.oauth.provider_json", side_effect=provider):
                    yield origin, password, selected
            finally:
                server.shutdown()
                thread.join()


def check(browser, width, out):
    with https_fixture() as (origin, password, selected), closing(browser.new_context(
            ignore_https_errors=True, viewport={"width": width, "height": 830},
            has_touch=width == 360, reduced_motion="reduce")) as context:
        page = context.new_page()
        page.set_default_timeout(8000)
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        auth_responses = []

        def record(response):
            path = urlsplit(response.url).path
            if path.startswith("/auth/"):
                auth_responses.append((path, response.status))

        page.on("response", record)
        cancel = [False]

        def reply(route):
            upstream = route.fetch(max_redirects=0)
            if upstream.status != 303:
                route.fulfill(response=upstream)
                return
            provider_url = urlsplit(upstream.headers["location"])
            assert provider_url.netloc in {"id.vk.ru", "oauth.yandex.ru"}
            fields = parse_qs(provider_url.query)
            callback = fields["redirect_uri"][0]
            assert callback.startswith(origin + "/auth/")
            response = {"state": fields["state"][0], "device_id": "browser-demo"}
            if cancel[0]:
                response["error"] = "access_denied"
                cancel[0] = False
            else:
                response["code"] = "fixture-code"
            route.fulfill(response=upstream, headers={**upstream.headers,
                          "location": callback + "?" + urlencode(response)})

        context.route(re.compile(re.escape(origin) + r"/auth/(?:vk|yandex)/start$"), reply)
        try:
            for count in (0, 1, 2):
                selected[0] = count
                assert page.goto(origin + "/login").status == 200
                if width == 360 and count == 0:
                    palette(page, "chalk")
                expect(page.locator(".dean-login [data-provider]")).to_have_count(count)
                expect(page.get_by_role("button", name="Войти", exact=True)).to_be_in_viewport()
                full_width(page, width)
                screenshot(page, out, f"login-{count}-providers-{width}")

            page.get_by_role("button", name="Показать", exact=True).click()
            expect(page.get_by_label("Пароль", exact=True)).to_have_attribute("type", "text")
            page.get_by_role("button", name="Скрыть", exact=True).click()
            expect(page.get_by_label("Пароль", exact=True)).to_have_attribute("type", "password")
            password_login(page, ACCOUNT, password)
            intro = page.locator(".dean-first-access")
            expect(intro.get_by_role("heading")).to_have_text("Подключи быстрый вход")
            assert intro.evaluate("(el) => el.matches(':modal')")
            screenshot(page, out, f"fast-entry-{width}")
            expect(page.locator(".dean-tour")).to_have_count(0)
            submit(page, "Позже", inside=intro)
            tour = page.locator(".dean-tour")
            expect(tour.get_by_role("heading")).to_have_text("Сегодня")
            submit(page, "Пропустить", inside=tour)

            page.goto(origin + SERVICE)
            page.locator('[name="purpose"]').fill(PURPOSE)
            submit(page, "Отправить заявку")
            page.wait_for_url(re.compile(re.escape(origin) + r"/requests/[a-f0-9]{32}$"))
            application = urlsplit(page.url).path
            article = page.locator("article")
            expect(article).to_contain_text(PURPOSE)

            page.goto(origin + "/profile/access")
            cancel[0] = True
            page.get_by_role("button", name="Привязать VK ID").click()
            page.wait_for_url(origin + "/profile/access?notice=cancelled")
            for provider in ("VK ID", "Яндекс ID"):
                page.get_by_role("button", name="Привязать " + provider).click()
                page.wait_for_url(origin + "/profile/access?notice=linked")
                expect(page.get_by_role("button", name="Отвязать " + provider)).to_be_visible()
            screenshot(page, out, f"access-methods-{width}")

            def logout():
                page.get_by_role("button", name="Выйти", exact=True).click()
                page.wait_for_url(origin + "/login")

            def from_provider(provider):
                page.goto(origin + "/login?" + urlencode({"next": application}))
                page.get_by_role("button", name="Войти через " + provider).click()
                page.wait_for_url(origin + application)
                expect(article).to_contain_text(PURPOSE)

            for provider in ("VK ID", "Яндекс ID"):
                logout()
                from_provider(provider)
            logout()
            password_login(page, ACCOUNT, password)
            page.goto(origin + "/profile/access")
            submit(page, "Отвязать VK ID", status=200)
            expect(page.get_by_role("heading", name="Удалить способ «VK ID»?")).to_be_visible()
            submit(page, "Да, удалить")
            page.goto(origin + "/profile/access")
            expect(page.get_by_role("button", name="Отвязать VK ID")).to_have_count(0)
            logout()
            from_provider("Яндекс ID")
            logout()
            page.goto(origin + "/login?" + urlencode({"next": application}))
            password_login(page, ACCOUNT, password)
            page.wait_for_url(origin + application)
            expect(article).to_contain_text(PURPOSE)
            assert not errors, errors
        except Exception as error:
            address = urlsplit(page.url)
            detail = f"{address.netloc}{address.path}, {auth_responses}: {error}"
            raise RuntimeError(detail.replace(password, "[скрыто]")) from None
    print(f"PASS {width}px · HTTPS, 0/1/2 провайдера, «Позже», отмена, привязка и то же заявление после входа", flush=True)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--screenshots", type=Path)
    args = parser.parse_args()
    if args.screenshots:
        args.screenshots.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as playwright, closing(playwright.chromium.launch(channel="chrome")) as browser:
        for width in (360, 1280):
            check(browser, width, args.screenshots)


if __name__ == "__main__":
    main()
