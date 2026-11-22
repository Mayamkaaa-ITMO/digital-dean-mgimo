import hashlib
import hmac
import json
import re
import stat
import subprocess
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from datetime import datetime, time, timedelta
from pathlib import Path
from unittest.mock import patch
from urllib.parse import parse_qs, urlencode, urlsplit

from dean import access_cli, access_paths, access_state, auth, clock, oauth, public_schedule, public_sync
from dean.access_config import AccessConfig
from dean.config import WebConfig
from dean.db import connect
from dean.domain import DomainError, timestamp
from dean.seed import ZONE
from dean.web import create_app
from tests.test_dean import Client, DemoFixture, Forms
from tests.test_public_schedule import GROUP, MONDAY, SOURCE

ORIGIN = "https://dean.example.test"
CONFIG = AccessConfig(max_name="demo_max_bot", max_token="synthetic-max-fixture", vk_client="1234",
                      yandex_client="fake-yandex", yandex_secret="synthetic-yandex-fixture", passkeys=True)
PERSONAL, MAX_PERSONAL = "demo-personal", "demo-max"
DEMO_PASSWORD = "synthetic-personal-fixture-password"
SESSION = "__Host-dean_session"
REMEMBER = "__Host-dean_remember"


class HttpsClient(Client):
    def request(self, path, fields=None, cookie_override=None, **overrides):
        return super().request(path, fields, cookie_override, **{
            "HTTP_HOST": "dean.example.test", "wsgi.url_scheme": "https", **overrides,
        })


class SeamlessAccessTest(DemoFixture):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        with closing(connect(cls.base)) as db:
            for username in (PERSONAL, MAX_PERSONAL):
                access_state.new_demo_account(db, username, DEMO_PASSWORD)
            week = public_schedule.week_scope(SOURCE, GROUP, MONDAY)
            public_sync.publish(db, public_sync.stage_sample(db, week))

    def setUp(self):
        super().setUp()
        self.app = create_app(self.path, WebConfig(ORIGIN), access=CONFIG)

    def browser(self):
        return HttpsClient(self.app)

    def password(self, browser, name, *, start="/login", remember=False):
        status, headers, body = browser.request(start)
        if status == 303:
            status, _, body = browser.request(headers["Location"])
        self.assertEqual(status, 200)
        submitted = {**Forms(body).forms["/login"], "username": name, "password": self.passwords.get(name, DEMO_PASSWORD)}
        if remember:
            submitted["remember"] = "1"
        return browser.request("/login", submitted, HTTP_ORIGIN=ORIGIN)

    def logout(self, browser, page="/"):
        fields = browser.fields(page, "/logout")
        self.assertEqual(browser.request("/logout", fields)[1]["Location"], "/login")

    def expect_owner(self, browser, username):
        self.assertEqual(browser.request("/profile")[0], 200)
        self.assertEqual(self.db_one("""
            SELECT a.username FROM session s JOIN account a ON a.id=s.account_id WHERE s.token_hash=?
        """, (auth.digest(browser.cookies[SESSION]),)), username)

    def post(self, browser, page, action, **form):
        return browser.request(action, {**browser.fields(page, action), **form}, HTTP_ORIGIN=ORIGIN)

    def oauth_start(self, browser, provider, begin="/profile/access", intent="link", target=""):
        path = f"/auth/{provider}/start"
        opts = {"intent": intent} if intent != "login" else {"next": target}
        response = self.post(browser, begin, path, **opts)
        self.assertEqual(response[0], 303, response[2][:500])
        remote = urlsplit(response[1]["Location"])
        state = parse_qs(remote.query)["state"][0]
        self.assertEqual(remote.scheme, "https")
        self.assertEqual(remote.netloc, "id.vk.ru" if provider == "vk" else "oauth.yandex.ru")
        self.assertEqual(parse_qs(remote.query)["redirect_uri"], [f"{ORIGIN}/auth/{provider}/callback"])
        if provider == "yandex":
            self.assertEqual(parse_qs(remote.query)["scope"], ["login:info"])
        proof = self.db_one("SELECT proof FROM identity_attempt WHERE token_hash=?", (auth.digest(state),))
        self.assertEqual(parse_qs(remote.query)["code_challenge"], [oauth.challenge(proof)])
        return state, proof

    def oauth_callback(self, browser, provider, state, subject, proof, *, invalid=None):
        def transport(uri, fields=None, authorization=None):
            address = urlsplit(uri)
            if address.path == "/oauth2/auth":
                self.assertEqual(address.query, "")
                self.assertEqual(fields, {"grant_type": "authorization_code",
                                         "redirect_uri": oauth.callback(ORIGIN, "vk"),
                                         "client_id": CONFIG.vk_client, "state": state,
                                         "code_verifier": proof, "device_id": "device-one",
                                         "code": "demo-provider-code"})
                return {"state": "wrong" if invalid else state, "user_id": subject, "access_token": "fictional-token"}
            if address.path == "/oauth2/user_info":
                self.fail("VK already returned the account ID in its token response")
            if address.path == "/token":
                self.assertEqual(fields["client_secret"], CONFIG.yandex_secret)
                self.assertEqual(fields["code_verifier"], proof)
                return {"access_token": "fictional-token"}
            self.assertEqual(address.netloc, "login.yandex.ru")
            self.assertEqual(authorization, "OAuth fictional-token")
            return {"id": subject, "client_id": "wrong" if invalid else CONFIG.yandex_client,
                    "default_email": "synthetic@example.test", "real_name": "Имя игнорируется"}
        query = urlencode({"code": "demo-provider-code", "state": state, "device_id": "device-one"})
        with patch("dean.oauth.provider_json", side_effect=transport):
            return browser.request(f"/auth/{provider}/callback?" + query)

    def oauth_login(self, browser, provider, subject, target="", invalid=None):
        state, proof = self.oauth_start(browser, provider, "/login", "login", target)
        return self.oauth_callback(browser, provider, state, subject, proof, invalid=invalid)

    def test_vk_requires_a_valid_server_identity_and_token(self):
        credentials = {"state": "attempt", "access_token": "test-token"}
        for extra in ({"user_id": 0}, {"user_id": True}, {"user_id": 88, "access_token": ""}):
            with self.subTest(extra=extra), patch.object(oauth, "provider_json", return_value={**credentials, **extra}) as api:
                with self.assertRaises(DomainError):
                    oauth.exchange(ORIGIN, CONFIG, "vk", "test-code", "attempt", "verifier", "device")
                self.assertEqual(api.call_count, 1)
        with patch.object(oauth, "provider_json", side_effect=[credentials, {"user": {"user_id": 88}}]) as api:
            self.assertEqual(oauth.exchange(ORIGIN, CONFIG, "vk", "test-code", "attempt", "verifier", "device"), "88")
            api.assert_called_with(oauth.VK + "/oauth2/user_info",
                                   {"client_id": CONFIG.vk_client, "access_token": "test-token"})

    def test_vk_transport_retries_only_before_credentials_are_sent(self):
        uri = oauth.VK + "/oauth2/auth"
        def reply(code, data):
            return subprocess.CompletedProcess([], code, stdout=data)
        with patch.object(oauth, "sleep"), patch.object(oauth.subprocess, "run", side_effect=[
            reply(35, b"\n000 0.000000 0"), reply(0, b"{}\n200 0.01 18"),
        ]) as transfer:
            self.assertEqual(oauth.mac_vk_response(uri, b"code=test"), b"{}")
            self.assertEqual(transfer.call_count, 2)
            self.assertEqual(transfer.call_args.kwargs["input"], b"code=test")
            self.assertNotIn("code=test", " ".join(transfer.call_args.args[0]))
        with patch.object(oauth.subprocess, "run", return_value=reply(56, b"\n000 0.01 18")) as sent:
            with self.assertRaises(DomainError):
                oauth.mac_vk_response(uri, b"code=test")
            sent.assert_called_once()

    def test_first_password_signin_offers_linking_and_remembers_dismissal(self):
        browser = self.browser()
        login = browser.request("/login")[2]
        self.assertIn("Войти через VK ID", login)
        self.assertIn("Войти через Яндекс ID", login)
        self.assertNotIn("Другие способы входа", login)
        target = "/schedule?view=week"
        start = access_paths.login_url(target)
        self.assertEqual(self.password(browser, PERSONAL, start=start)[1]["Location"], target)
        _, headers, body = browser.request(target)
        self.assertIn('class="dean-first-access"', body)
        self.assertIn("Подключить VK ID", body)
        self.assertIn("Подключить Яндекс ID", body)
        self.assertIn("https://id.vk.ru https://oauth.yandex.ru", headers["Content-Security-Policy"])
        state, proof = self.oauth_start(browser, "vk", begin=target)
        result = self.oauth_callback(browser, "vk", state, "12345", proof)
        self.assertEqual(result[0], 303)
        methods = result[1]["Location"]
        self.assertEqual(parse_qs(urlsplit(methods).query)["next"], [target])
        self.assertIn("Привязать Яндекс ID", browser.request(methods)[2])
        direction = self.post(browser, methods, "/profile/access/later")
        self.assertEqual(direction[1]["Location"], target)
        self.assertNotIn('class="dean-first-access"', browser.request(target)[2])
        self.assertEqual(self.db_one("SELECT count(*) FROM access_intro_dismissal"), 1)
        self.logout(browser)
        new = self.browser()
        self.assertEqual(self.oauth_login(new, "vk", "12345", target)[1]["Location"], target)
        self.expect_owner(new, PERSONAL)
        self.assertNotIn('class="dean-first-access"', new.request(target)[2])

    def max_begin(self, browser, begin, *, invitation="", intent="", target=""):
        path = "/auth/max/start"
        result = self.post(browser, begin, path, **({"intent": intent} if intent else {
            "invitation": invitation, "next": target,
        }))
        self.assertEqual(result[0], 303, result[2][:600])
        wait = result[1]["Location"]
        state = wait.split("/")[-1]
        pin = re.search(r'class="auth-pin"><strong>([0-9]{6})', browser.request(wait)[2])[1]
        return wait, state, pin

    def signed_data(self, state, subject=67890, *, age=0):
        pairs = {"auth_date": str(int(auth.real_now().timestamp()) - age), "start_param": state,
                 "user": json.dumps({"id": subject, "first_name": "Имя игнорируется"}, ensure_ascii=False)}
        payload = "\n".join(f"{key}={value}" for key, value in sorted(pairs.items())).encode()
        secret = hmac.new(b"WebAppData", CONFIG.max_token.encode(), hashlib.sha256).digest()
        return urlencode({**pairs, "hash": hmac.new(secret, payload, hashlib.sha256).hexdigest()})

    def max_approve(self, state, pin, *, subject=67890, age=0):
        miniapp = self.browser()
        return miniapp.request("/auth/max/approve", {
            "pin": pin, "init_data": self.signed_data(state, subject, age=age),
        }, HTTP_ORIGIN=ORIGIN, HTTP_SEC_FETCH_SITE="same-origin")

    def test_guest_login_return_to_group_and_independent_shared_passwords(self):
        guest = self.browser()
        html = guest.request("/login")[2]
        self.assertIn("Войти через MAX", html)
        forms = Forms(html).forms
        self.assertTrue({"username", "password"}.issubset(forms["/login"]))
        self.assertLess(html.index('action="/login"'), html.index('action="/auth/max/start"'))
        self.assertEqual(set(forms["/auth/max/start"]), {"csrf", "next"})
        self.assertIn('href="/universities"', html)
        listing = guest.request("/universities")[2]
        self.assertIn("516-1", listing)
        group = "/universities/group?" + urlencode({"source": SOURCE, "group": GROUP, "week": MONDAY})
        public = guest.request(group)[2]
        self.assertIn("08:50–10:25", public)
        self.assertIn(access_paths.login_url(group), public)
        for username in ("student-a", "student-b", "staff"):
            client = self.browser()
            start = access_paths.login_url(group) if username == "student-a" else "/login"
            with patch("dean.oauth.provider_json", side_effect=ConnectionError("test offline")) as transport:
                result = self.password(client, username, start=start, remember=True)
                transport.assert_not_called()
            self.assertEqual(result[1]["Location"], group if username == "student-a" else "/staff" if username == "staff" else "/")
            issued = [value for name, value in client.last_headers if name == "Set-Cookie" and value.startswith(SESSION)]
            self.assertEqual(len(issued), 1)
            self.assertNotIn("Max-Age", issued[0])
            self.assertNotIn(REMEMBER, client.cookies)
            if username != "staff":
                with clock.at(datetime.combine(self.today, time(7), ZONE)):
                    today = client.request("/")[2]
                for name in ("Расписание", "Успеваемость", "Заявления", "Уведомления", "Все сервисы", "Ближайшая пара"):
                    self.assertIn(name, today)
                self.assertIn("Бронирования", client.request("/all-services")[2])
                methods = client.request("/profile/access")[2]
                self.assertNotIn("Привязать VK ID", methods)
                self.assertIn("Сменить аккаунт", methods)
                self.assertIn(username, methods)
                switch = self.post(client, "/profile/access", "/logout", next="/profile/access")
                self.assertEqual(switch[1]["Location"], access_paths.login_url("/profile/access"))
        disabled = HttpsClient(create_app(self.path, WebConfig(ORIGIN), access=AccessConfig()))
        self.assertNotIn("/auth/max/start", disabled.request("/login")[2])
        self.assertEqual(self.password(disabled, "student-b")[0], 303)
        local = Client(create_app(self.path, WebConfig(), access=AccessConfig()))
        self.assertNotIn("MAX", local.request("/login")[2])
        self.assertEqual(local.login(PERSONAL, DEMO_PASSWORD)[0], 303)
        self.assertNotIn("MAX", local.request("/profile/access")[2])

    def test_deep_link_logout_and_second_student(self):
        shared = self.browser()
        self.password(shared, "student-a")
        application, payload = self.submit(shared, "Синтетическое заявление владельца.")
        old = shared.cookies[SESSION]
        self.assertEqual(shared.request(application)[0], 200)
        self.logout(shared, application)
        requested = shared.request(application)
        self.assertEqual(requested[1]["Location"], access_paths.login_url(application))
        self.assertEqual(self.password(shared, "student-b", start=application)[1]["Location"], application)
        self.expect_owner(shared, "student-b")
        denied = shared.request(application)
        self.assertEqual(denied[0], 404)
        self.assertIn("В мой кабинет", denied[2])
        self.assertNotIn("Синтетическое заявление владельца", denied[2])
        self.assertEqual(shared.request(application, cookie_override=SESSION + "=" + old)[0], 303)
        self.logout(shared)
        self.assertEqual(shared.request("/services/consultation", payload)[0], 403)
        self.assertEqual(self.password(shared, "student-a", start=application)[1]["Location"], application)
        self.assertIn("Синтетическое заявление владельца", shared.request(application)[2])
        for unsafe in ("https://outside.test", "//outside.test", "/%2foutside.test", "/logout", application + "/send"):
            error = self.browser()
            target = "/login?" + urlencode({"next": unsafe})
            self.assertEqual(self.password(error, "student-a", start=target)[1]["Location"], "/")

    def test_remember_rotation_across_restart_and_device_revocation(self):
        owner = self.browser()
        self.assertEqual(self.password(owner, PERSONAL, remember=True)[0], 303)
        secret = owner.cookies[REMEMBER]
        self.assertTrue(any(key == "Set-Cookie" and val.startswith(REMEMBER) and "; Secure" in val
                            and "Max-Age=" in val for key, val in owner.last_headers))
        app, _ = self.submit(owner)
        row_hash = self.db_one("SELECT secret_hash FROM trusted_device")
        self.assertEqual(row_hash, auth.digest(secret))
        restarted = create_app(self.path, WebConfig(ORIGIN), access=CONFIG)
        tabs = [HttpsClient(restarted), HttpsClient(restarted)]
        for tab in tabs:
            tab.cookies[REMEMBER] = secret
        with ThreadPoolExecutor(max_workers=2) as workers:
            statuses = list(workers.map(lambda tab: tab.request(app)[0], tabs))
        self.assertEqual(statuses, [200, 200])
        self.assertEqual(sum(tab.cookies[REMEMBER] != secret for tab in tabs), 1)
        self.assertEqual(self.db_one("SELECT count(*) FROM trusted_device"), 1)
        new = next(tab.cookies[REMEMBER] for tab in tabs if tab.cookies[REMEMBER] != secret)
        now = auth.real_now()
        with patch("dean.auth.real_now", return_value=now + timedelta(seconds=15)):
            expired = HttpsClient(restarted)
            expired.cookies[REMEMBER] = secret
            self.assertEqual(expired.request(app)[0], 303)
        separate = self.browser()
        self.password(separate, PERSONAL)
        device = self.db_one("SELECT id FROM trusted_device")
        self.assertEqual(self.post(separate, "/profile/sessions", f"/profile/devices/{device}/revoke")[0], 303)
        for tab in tabs:
            self.assertEqual(tab.request(app)[0], 303)
        expired = self.browser()
        expired.cookies[REMEMBER] = new
        self.assertEqual(expired.request(app)[0], 303)

    def test_remember_deadline_idle_logout_and_password_revocation(self):
        current = auth.real_now()
        browser = self.browser()
        with patch("dean.auth.real_now", return_value=current):
            self.password(browser, PERSONAL, remember=True)
            quit_form = browser.fields("/", "/logout")
        with patch("dean.auth.real_now", return_value=current + auth.SESSION_IDLE + timedelta(seconds=1)):
            self.assertEqual(browser.request("/logout", quit_form)[0], 303)
            self.assertEqual(self.db_one("SELECT COUNT(*) FROM trusted_device"), 0)
        with patch("dean.auth.real_now", return_value=current):
            self.password(browser, PERSONAL, remember=True)
            self.assertEqual(self.db_one("SELECT expires_at FROM trusted_device"), timestamp(current + auth.DEVICE_TTL))
        with patch("dean.auth.real_now", return_value=current + auth.DEVICE_TTL):
            self.assertEqual(browser.request("/profile")[0], 303)
        clean = self.browser()
        self.password(clean, PERSONAL, remember=True)
        with closing(connect(self.path)) as db:
            db.execute("UPDATE account SET password_hash=? WHERE username=?",
                       (auth.hash_password("new-synthetic-reset-password"), PERSONAL))
        self.assertEqual(clean.request("/profile")[0], 303)
        self.assertEqual(self.db_one("SELECT COUNT(*) FROM trusted_device"), 0)
        self.assertEqual(self.db_one("SELECT COUNT(*) FROM session"), 0)

    def test_same_application_for_vk_yandex_max_and_password_after_unlink(self):
        owner = self.browser()
        self.assertEqual(self.password(owner, PERSONAL)[0], 303)
        app, _ = self.submit(owner, "Сохранить номер заявления во всех способах входа.")
        account_id = self.db_one("SELECT student_id FROM application WHERE id=?", (app.rsplit("/", 1)[1],))
        for provider, subject in (("vk", "95031"), ("yandex", "67219")):
            state, proof = self.oauth_start(owner, provider)
            self.assertEqual(self.oauth_callback(owner, provider, state, subject, proof)[1]["Location"],
                             "/profile/access?notice=linked")
        wait, state, pin = self.max_begin(owner, "/profile/access", intent="link")
        self.assertEqual(self.max_approve(state, pin)[0], 200)
        self.assertEqual(self.post(owner, wait, wait + "/finish")[1]["Location"], "/profile/access?notice=linked")
        self.logout(owner)
        for provider, subject in (("vk", "95031"), ("yandex", "67219")):
            new = self.browser()
            outcome = self.oauth_login(new, provider, subject, app)
            self.assertEqual(outcome[1]["Location"], app)
            self.expect_owner(new, PERSONAL)
            self.assertIn("Сохранить номер заявления", new.request(app)[2])
            self.logout(new)
        self.password(owner, PERSONAL)
        remove = "/auth/vk/unlink"
        first = self.post(owner, "/profile/access", remove)
        self.assertIn("Да, удалить", first[2])
        self.assertEqual(owner.request(remove, Forms(first[2]).forms[remove])[0], 303)
        self.logout(owner)
        vk = self.oauth_login(self.browser(), "vk", "95031", app)
        self.assertEqual(vk[0], 403)
        self.assertIn("пока не привязан", vk[2])
        self.assertEqual(Forms(vk[2]).forms["/login"]["next"], app)
        ya = self.browser()
        self.assertEqual(self.oauth_login(ya, "yandex", "67219", app)[1]["Location"], app)
        self.expect_owner(ya, PERSONAL)
        self.assertIn("Сохранить номер заявления", ya.request(app)[2])
        max_client = self.browser()
        wait, state, pin = self.max_begin(max_client, "/login", target=app)
        self.assertEqual(self.max_approve(state, pin)[0], 200)
        self.assertEqual(self.post(max_client, wait, wait + "/finish")[1]["Location"], app)
        self.expect_owner(max_client, PERSONAL)
        self.assertIn("Сохранить номер заявления", max_client.request(app)[2])
        self.assertEqual(self.password(owner, PERSONAL, start=app)[1]["Location"], app)
        self.assertEqual(self.db_one("SELECT account_id FROM identity_link WHERE provider='yandex'"), account_id)
        self.assertEqual(self.db_one("SELECT subject_hash FROM identity_link WHERE provider='yandex'"),
                         hashlib.sha256(b"67219").hexdigest())
        self.assertEqual(self.db_one("SELECT COUNT(*) FROM application WHERE id=?", (app.rsplit("/", 1)[1],)), 1)

    def test_max_initial_invite_is_one_use_and_bound_to_browser(self):
        with closing(connect(self.path)) as db:
            account = db.execute("SELECT id FROM account WHERE username=?", (MAX_PERSONAL,)).fetchone()[0]
            invite = access_state.invite(db, account)
        original, other = self.browser(), self.browser()
        wait, state, pin = self.max_begin(original, "/auth/max/invite", invitation=invite)
        another_wait, another_state, another_pin = self.max_begin(other, "/auth/max/invite", invitation=invite)
        self.assertNotEqual(state, another_state)
        self.assertEqual(other.request(wait)[0], 403)
        self.assertEqual(self.max_approve(state, pin, age=301)[0], 403)
        self.assertEqual(self.browser().request("/auth/max/approve", {"pin": pin, "init_data": self.signed_data(state)})[0], 403)
        self.assertEqual(self.max_approve(state, "000000" if pin != "000000" else "123456")[0], 422)
        self.assertEqual(self.max_approve(state, pin)[0], 200)
        self.assertEqual(self.max_approve(state, pin)[0], 409)
        self.assertNotIn(SESSION, original.cookies)
        self.assertIn("Ждём подтверждение", self.post(other, another_wait, another_wait + "/finish")[2])
        self.assertEqual(self.post(original, wait, wait + "/finish")[1]["Location"], "/")
        self.expect_owner(original, MAX_PERSONAL)
        self.assertEqual(self.max_approve(another_state, another_pin, subject=71845)[0], 200)
        self.assertEqual(self.post(other, another_wait, another_wait + "/finish")[0], 410)
        self.assertNotIn(SESSION, other.cookies)
        self.assertEqual(original.request(wait)[0], 410)
        self.assertEqual(self.db_one("SELECT COUNT(*) FROM max_invitation"), 0)
        for provider in ("vk", "yandex"):
            state, proof = self.oauth_start(original, provider)
            self.assertEqual(self.oauth_callback(original, provider, state, "99011", proof)[0], 303)
        self.assertEqual(self.db_one("SELECT COUNT(*) FROM identity_link WHERE account_id=?", (account,)), 3)

    def test_cancellation_stale_confirmation_conflicting_link_and_provider_validation(self):
        owner = self.browser()
        self.password(owner, PERSONAL)
        state, proof = self.oauth_start(owner, "vk")
        stranger = self.browser()
        self.password(stranger, MAX_PERSONAL)
        self.assertEqual(self.oauth_callback(stranger, "vk", state, "1234", proof)[0], 403)
        self.assertEqual(self.oauth_callback(owner, "vk", state, "1234", proof)[0], 303)
        other, verifier = self.oauth_start(stranger, "vk")
        self.assertEqual(self.oauth_callback(stranger, "vk", other, "1234", verifier)[0], 409)
        self.assertEqual(self.db_one("SELECT COUNT(*) FROM identity_link"), 1)
        unsigned = self.browser()
        invalid = self.oauth_login(unsigned, "vk", "1234", invalid=True)
        self.assertEqual(invalid[0], 303)
        self.assertEqual(invalid[1]["Location"], access_paths.login_url("", "provider"))
        self.assertIn(oauth.FAIL, unsigned.request(invalid[1]["Location"])[2])
        invalid = self.oauth_login(unsigned, "yandex", "1234", invalid=True)
        self.assertEqual(invalid[0], 303)
        self.assertEqual(invalid[1]["Location"], access_paths.login_url("", "provider"))
        self.assertNotIn(SESSION, unsigned.cookies)
        target = "/requests"
        state, _ = self.oauth_start(unsigned, "yandex", "/login", "login", target)
        cancelled = unsigned.request(f"/auth/yandex/callback?state={state}&error=access_denied")
        self.assertEqual(cancelled[1]["Location"], access_paths.login_url(target, "cancelled"))
        self.assertEqual(unsigned.request(f"/auth/yandex/callback?state={state}&error=access_denied")[0], 410)
        wait, state, _ = self.max_begin(unsigned, "/login", target=target)
        self.assertEqual(self.post(unsigned, wait, wait + "/cancel")[1]["Location"], access_paths.login_url(target, "cancelled"))
        wait, state, pin = self.max_begin(unsigned, "/login")
        with patch("dean.auth.real_now", return_value=auth.real_now() + access_state.ATTEMPT_TTL + timedelta(seconds=1)):
            self.assertEqual(self.max_approve(state, pin)[0], 410)
            self.assertEqual(unsigned.request(wait)[0], 410)
        removal = "/auth/vk/unlink"
        saved_form = {**owner.fields("/profile/access", removal), "confirm": "1"}
        with patch("dean.auth.real_now", return_value=auth.real_now() + auth.CONFIRM_TTL + timedelta(seconds=1)):
            self.assertEqual(owner.request(removal, saved_form)[0], 403)
            self.assertEqual(self.post(owner, "/profile/access", "/profile/access/confirm",
                                       password=DEMO_PASSWORD)[0], 303)
            self.assertEqual(owner.request(removal, saved_form)[0], 303)

    def test_today_lists_a_returned_application_and_reopen_issues_owned_pdf(self):
        owner, staff, other = self.browser(), self.browser(), self.browser()
        for client, username in ((owner, PERSONAL), (staff, "staff"), (other, "student-b")):
            self.password(client, username)
        service = "/services/enrolment-certificate"
        response = self.post(owner, service, service, purpose="Учебный архив")
        app = response[1]["Location"]
        review = "/staff" + app
        self.assertEqual(self.post(staff, app, review)[0], 303)
        self.assertEqual(self.post(staff, app, review + "/needs_changes", note="Уточни цель выдачи.")[0], 303)
        today = owner.request("/")[2]
        self.assertIn("Заявление на доработке", today)
        self.assertIn("Уточни цель выдачи.", today)
        self.assertIn(f'href="{app}" class="">Исправить', today)
        self.assertNotIn("Уточни цель выдачи.", other.request("/")[2])
        self.logout(owner)
        self.assertEqual(self.password(owner, PERSONAL, start=app)[1]["Location"], app)
        edit = {**owner.fields(app, app + "/save"), "purpose": "Для личного архива"}
        self.assertEqual(owner.request(app + "/send", edit)[0], 303)
        self.assertEqual(self.post(staff, app, review)[0], 303)
        self.assertEqual(self.post(staff, app, review)[0], 303)
        document = "/documents/" + self.db_one("SELECT id FROM document")
        self.assertTrue(owner.request(document)[2].startswith(b"%PDF"))
        self.assertEqual(other.request(document)[0], 404)

    def test_key_cabinet_flow_and_replay_using_verifier_double(self):
        owner = self.browser()
        self.password(owner, PERSONAL)
        app, _ = self.submit(owner)
        key = "dGVzdC1maXh0dXJl"
        credential = json.dumps({"id": key, "rawId": key, "type": "public-key", "response": {}})
        with patch("dean.access_config.find_spec", return_value=True), patch(
            "dean.passkeys.options", return_value={"challenge": "generated-by-test-double"},
        ), patch("dean.passkeys.registration", return_value=(key, b"test-key", 0)), patch(
            "dean.passkeys.authentication", return_value=1,
        ):
            csrf = owner.fields("/profile/access", "/profile/access")["csrf"]
            issued = owner.request("/auth/key/start", {"csrf": csrf, "intent": "link"})
            self.assertEqual(issued[0], 200)
            response = {"csrf": csrf, "state": json.loads(issued[2])["state"], "credential": credential}
            self.assertEqual(owner.request("/auth/key/finish", response)[0], 200)
            self.assertEqual(owner.request("/auth/key/finish", response)[0], 410)
            login = self.browser()
            params = login.fields(access_paths.login_url(app), "/auth/key/start")
            attempt = login.request("/auth/key/start", params)
            self.assertEqual(attempt[0], 200)
            done = login.request("/auth/key/finish", {"csrf": params["csrf"], "credential": credential,
                                                      "state": json.loads(attempt[2])["state"]})
            self.assertEqual(json.loads(done[2])["return"], app)
            self.expect_owner(login, PERSONAL)
            key_id = self.db_one("SELECT id FROM access_key")
            action = f"/auth/key/{key_id}/unlink"
            self.assertEqual(self.post(owner, "/profile/access", action, confirm="1")[0], 303)
            self.assertEqual(login.request(app)[0], 303)

    def test_operator_files_are_private_and_preserve_shared_accounts(self):
        path = Path(self.workspace.name) / "new-account.json"
        access_cli.run(self.path, "demo-access", "demo-extra", path)
        self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
        self.assertEqual(self.db_one("SELECT COUNT(*) FROM account WHERE username IN ('student-a','student-b','staff')"), 3)
        with self.assertRaises(FileExistsError):
            access_cli.run(self.path, "demo-access", "demo-extra", path)
        secret = json.loads(path.read_text())["demo-extra"]
        entry = self.browser()
        self.assertEqual(entry.login("demo-extra", secret)[0], 303)
        self.expect_owner(entry, "demo-extra")
