import io
import json
import os
import socket
import stat
import subprocess
import sys
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from datetime import datetime, timedelta
from html.parser import HTMLParser
from http.client import HTTPConnection
from pathlib import Path
from urllib.parse import urlencode, urlsplit
from uuid import uuid4
from wsgiref.util import setup_testing_defaults
from wsgiref.validate import validator
from zoneinfo import ZoneInfo

from dean.access_paths import login_url
from dean.config import WebConfig
from dean.db import ROOT, backup, connect, migrate, schema_version
from dean.seed import UNIVERSITY, credentials_path, seed, seed_id
from dean.web import create_app


class Forms(HTMLParser):
    def __init__(self, html):
        super().__init__()
        self.forms = {}
        self.current = None
        self.feed(html)

    def handle_starttag(self, tag, attributes):
        attrs = dict(attributes)
        if tag == "form":
            self.current = self.forms.setdefault(attrs["action"], {})
        if tag == "input" and self.current is not None and attrs.get("name"):
            if attrs.get("type") in {"checkbox", "radio"} and "checked" not in attrs:
                return
            self.current[attrs["name"]] = attrs.get("value", "")

    def handle_endtag(self, tag):
        if tag == "form":
            self.current = None


class Client:
    def __init__(self, app):
        self.app = validator(app)
        self.cookies = {}

    def request(self, path, fields=None, cookie_override=None, **overrides):
        split = urlsplit(path)
        data = fields if isinstance(fields, bytes) else urlencode(fields or {}).encode()
        cookie_header = "; ".join(f"{k}={v}" for k, v in self.cookies.items())
        env = {
            "SCRIPT_NAME": "",
            "PATH_INFO": split.path,
            "QUERY_STRING": split.query,
            "REQUEST_METHOD": "GET" if fields is None else "POST",
            "wsgi.input": io.BytesIO(data),
            "REMOTE_ADDR": "127.0.0.1",
            "HTTP_COOKIE": cookie_header if cookie_override is None else cookie_override,
        }
        if fields is not None:
            env.update(CONTENT_TYPE="application/x-www-form-urlencoded", CONTENT_LENGTH=str(len(data)))
        env.update(overrides)
        setup_testing_defaults(env)
        status, headers = None, []

        def start_response(line, pairs, exc_info=None):
            nonlocal status, headers
            status, headers = int(line.split()[0]), pairs

        response = self.app(env, start_response)
        try:
            data = b"".join(response)
            media_type = dict(headers).get("Content-Type", "")
            html = data if media_type in {"application/pdf", "application/octet-stream"} or media_type.startswith("image/") else data.decode()
        finally:
            response.close()
        self.last_headers = headers
        for name, value in headers:
            if name == "Set-Cookie":
                key, content = value.split(";", 1)[0].split("=", 1)
                self.cookies[key] = content
        return status, dict(headers), html

    def fields(self, page, action):
        status, _, body = self.request(page)
        if status != 200:
            raise AssertionError((status, page))
        return Forms(body).forms[action]

    def login(self, username, password):
        form = self.fields("/login", "/login")
        return self.request("/login", {**form, "username": username, "password": password})


class DemoFixture(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.today = datetime.now(ZoneInfo("Europe/Moscow")).date()
        cls.temp = tempfile.TemporaryDirectory()
        cls.base = Path(cls.temp.name) / "base.sqlite3"
        with closing(connect(cls.base)) as db:
            migrate(db)
            seed(db, cls.base, cls.today)
            cls.period = db.execute("SELECT period_id FROM account WHERE username = 'student-a'").fetchone()[0]
        cls.passwords = json.loads(credentials_path(cls.base).read_text())

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def setUp(self):
        self.workspace = tempfile.TemporaryDirectory(dir=self.temp.name)
        self.addCleanup(self.workspace.cleanup)
        self.path = Path(self.workspace.name) / "live.sqlite3"
        with closing(connect(self.base)) as base, closing(connect(self.path)) as db:
            base.backup(db)
        credentials_path(self.path).write_text(json.dumps(self.passwords))
        self.app = create_app(self.path)

    def client(self, login=None):
        client = Client(self.app)
        if login:
            status, headers, _ = client.login(login, self.passwords[login])
            self.assertEqual(status, 303)
            self.assertEqual(headers["Location"], "/staff" if login == "staff" else "/")
            self.assertIn("HttpOnly", headers["Set-Cookie"])
        return client

    def db_one(self, sql, args=()):
        with closing(connect(self.path)) as db:
            return db.execute(sql, args).fetchone()[0]

    def http_worker(self, client, method, route, content=None):
        left, right = socket.socketpair()
        with left, right:
            env = {**os.environ, "DEAN_DB": str(self.path)}
            env.update(DEAN_PUBLIC_ORIGIN="", DEAN_SECURE_COOKIES="0", DEAN_TRUST_PROXY="0")
            proc = subprocess.Popen([sys.executable, "-m", "tests.http_worker", str(right.fileno())],
                                    env=env, pass_fds=(right.fileno(),), cwd=ROOT,
                                    stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
            right.close()
            left.settimeout(7)
            connection = HTTPConnection("127.0.0.1")
            connection.sock = left
            try:
                payload = urlencode(content).encode() if content is not None else None
                headers = {"Cookie": "dean_session=" + client.cookies["dean_session"]}
                if payload is not None:
                    headers["Content-Type"] = "application/x-www-form-urlencoded"
                connection.request(method, route, body=payload, headers=headers)
                response = connection.getresponse()
                result = response.status, response.getheader("Location"), response.read().decode()
                _, log = proc.communicate(timeout=7)
                self.assertEqual(proc.returncode, 0, log.decode())
                return proc.pid, result
            finally:
                connection.close()
                if proc.poll() is None:
                    proc.kill()
                    proc.communicate()

    def read_after_restart(self, client, route):
        _, (status, _, html) = self.http_worker(client, "GET", route)
        self.assertEqual(status, 200)
        return html

    def submit(self, student, details="Уточнить дату консультации."):
        fields = student.fields("/services/consultation", "/services/consultation")
        fields["details"] = details
        status, headers, _ = student.request("/services/consultation", fields)
        self.assertEqual(status, 303)
        return headers["Location"], fields

class DeanTest(DemoFixture):
    def test_student_views_current_previous_semester_and_zero_score(self):
        a = self.client("student-a")
        b = self.client("student-b")
        self.assertIn("Мира Яроветова", a.request("/profile")[2])
        self.assertIn("Лев Светолесов", b.request("/profile")[2])
        self.assertIn("Алгоритмы и структуры данных", a.request("/")[2])
        self.assertIn("Общая физика", b.request("/schedule?view=week")[2])
        self.assertIn("Ада Кремнева", a.request("/schedule")[2])
        lesson = a.request(f"/schedule/{seed_id('a1')}")[2]
        self.assertIn("демонстрационный набор", lesson)
        self.assertIn("ДЕМО-ИКН-21", lesson)
        body = b.request("/grades")[2]
        self.assertIn("0 / 100", body)
        self.assertIn("Экзамен предстоит", body)
        self.assertIn("Сессия", body)
        previous = a.request("/grades?period=demo-previous")[2]
        self.assertIn("Иностранный язык", previous)
        self.assertNotIn("Алгоритмы и структуры данных", previous)
        self.assertIn("На этот день занятий нет", a.request("/schedule?date=2030-01-01")[2])
        self.assertIn("Заявок пока нет", a.request("/requests")[2])

    def test_application_lifecycle_retry_restart_and_seed(self):
        a, b, staff = (self.client(name) for name in ("student-a", "student-b", "staff"))
        url, fields = self.submit(a)
        replay = a.request("/services/consultation", fields)
        self.assertEqual((replay[0], replay[1]["Location"]), (303, url))
        self.assertEqual(a.request("/services/consultation", {**fields, "details": "Другой вопрос"})[0], 409)
        self.assertIn("Отправлена", a.request(url)[2])
        self.assertNotIn(url, b.request("/requests")[2])
        self.assertIn(url, staff.request("/staff?status=submitted")[2])

        path = "/staff" + url
        payload = staff.fields(url, path)
        invalid = staff.request(path, {**payload, "status": "completed"})
        self.assertEqual(invalid[0], 409)
        self.assertIn("Переход недоступен", invalid[2])
        self.assertEqual(staff.request(path, payload)[0], 303)
        self.assertEqual(staff.request(path, payload)[0], 303)
        self.assertIn("Рассматривается", a.request(url)[2])
        completed = staff.fields(url, path)
        self.assertEqual(staff.request(path, {**completed, "note": "Консультация в 15:00."})[0], 303)
        self.assertEqual(self.db_one("SELECT COUNT(*) FROM application_history WHERE application_id = ?",
                                     (url.rsplit("/", 1)[1],)), 3)
        self.assertIn("Заявка: выполнена", a.request("/notifications")[2])

        env = {**os.environ, "DEAN_DB": str(self.path), "DEAN_DEMO_DATE": "2030-01-01"}
        subprocess.run([sys.executable, "-m", "dean", "seed"], cwd=ROOT, env=env,
                       check=True, capture_output=True)
        cookie = a.cookies["dean_session"]
        persisted = subprocess.run(
            [sys.executable, "-c",
             "import sys; from pathlib import Path; from dean.web import create_app; "
             "from tests.test_dean import Client; c = Client(create_app(Path(sys.argv[1]))); "
             "c.cookies['dean_session'] = sys.stdin.readline().strip(); print(c.request(sys.argv[2])[2])",
             str(self.path), url], cwd=ROOT, input=cookie + "\n", text=True, check=True, capture_output=True,
        )
        self.assertIn("Консультация в 15:00.", persisted.stdout)
        logout = a.fields(url, "/logout")
        self.assertEqual(a.request("/logout", logout)[0], 303)
        revoked = a.request(url, cookie_override="dean_session=" + cookie)
        self.assertEqual(revoked[1]["Location"], login_url(url))
        self.assertEqual(a.login("student-a", self.passwords["student-a"])[0], 303)
        self.assertIn("Выполнена", a.request(url)[2])

    def test_read_and_write_are_scoped_and_require_csrf(self):
        a, b, staff = (self.client(name) for name in ("student-a", "student-b", "staff"))
        url, _ = self.submit(a, "Текст заявки для проверки приватности.")
        aid = seed_id("a1")
        grade = seed_id(f"student-a:algorithms:{self.period}")
        notice = self.db_one("SELECT id FROM notification WHERE student_id = ? AND event_key LIKE 'application:%'",
                             (seed_id("student-a"),))
        for path in (url, f"/schedule/{aid}", f"/grades/{grade}", f"/announcements/{seed_id('welcome')}"):
            self.assertEqual(b.request(path)[0], 404, path)
            self.assertEqual(self.client().request(path)[1]["Location"], login_url(path))
        profile = b.request(f"/profile?student_id={seed_id('student-a')}&role=staff")[2]
        self.assertIn("Лев Светолесов", profile)
        self.assertNotIn("Мира Яроветова", profile)
        b_csrf = b.fields("/profile", "/logout")["csrf"]
        self.assertEqual(b.request(f"/notifications/{notice}/read", {"csrf": b_csrf})[0], 404)
        self.assertEqual(b.request("/staff" + url, {"csrf": b_csrf, "role": "staff", "status": "review"})[0], 403)
        self.assertEqual(b.request("/staff?role=staff")[0], 403)
        self.assertEqual(a.request("/services/consultation", {"details": "Запрос без CSRF"})[0], 403)
        self.assertEqual(staff.request(f"/requests/{seed_id('b-application')}")[0], 404)
        self.assertEqual(staff.request(f"/grades/{grade}")[0], 403)
        self.assertEqual(staff.request(f"/staff/schedule/{seed_id('b1')}")[0], 404)
        invalid = self.client().login("student-a", "invalid-password")
        self.assertEqual(invalid[0], 422)
        self.assertNotIn("Мира Яроветова", invalid[2])

    def test_staff_reschedule_and_publish_to_own_group(self):
        staff, a, b = (self.client(name) for name in ("staff", "student-a", "student-b"))
        eid = seed_id("a1")
        path = f"/staff/schedule/{eid}"
        fields = staff.fields(path, path)
        stale = staff.fields(path, path)
        invalid = staff.request(path, {**fields, "ends": fields["starts"]})
        self.assertEqual(invalid[0], 422)
        change = {**fields, "room": "ДЕМО · А-501", "starts": f"{self.today}T11:00",
                  "ends": f"{self.today}T12:30"}
        self.assertEqual(staff.request(path, change)[0], 303)
        self.assertEqual(staff.request(path, change)[0], 303)
        self.assertEqual(staff.request(path, stale)[0], 409)

        detail = a.request(f"/schedule/{eid}")[2]
        self.assertIn("ДЕМО · А-501", detail)
        self.assertIn("ДЕМО · А-214", detail)
        self.assertIn("11:00", detail)
        self.assertIn("Было", detail)
        self.assertEqual(self.db_one("SELECT COUNT(*) FROM schedule_revision"), 1)
        publish = "/staff/announcements"
        self.assertEqual(a.request(publish)[0], 403)
        self.assertIn(publish, staff.request("/staff")[2])
        form = staff.fields(publish, publish)
        form.update(title="Тестовое объявление", body="Проверка доступа <script>.")
        denied = staff.request(publish, {**form, "group": "demo-energy"})
        self.assertEqual(denied[0], 403)
        form["group"] = "demo-ai"
        status, headers, _ = staff.request(publish, form)
        self.assertEqual(status, 303)
        target = headers["Location"]
        self.assertEqual(staff.request(publish, form)[1]["Location"], target)
        history = staff.request(publish)[2]
        self.assertIn(target, history)
        self.assertIn("Получателей: 1", history)
        self.assertIn("&lt;script&gt;", a.request(target)[2])
        self.assertEqual(b.request(target)[0], 404)
        notices = a.request("/notifications")[2]
        self.assertEqual(notices.count("Тестовое объявление"), 1)
        self.assertEqual(notices.count("Изменилось расписание"), 1)
        news = a.request("/notifications?kind=announcement")[2]
        self.assertIn(target, news)
        self.assertIn("&lt;script&gt;", news)
        self.assertNotIn(target, a.request("/notifications?kind=changes")[2])
        b_notices = b.request("/notifications")[2]
        self.assertNotIn("Тестовое объявление", b_notices)
        self.assertNotIn("Изменилось расписание", b_notices)

    def test_staff_schedule_opens_one_week_and_keeps_group_scope(self):
        staff = self.client("staff")
        monday = self.today - timedelta(days=self.today.weekday())
        next_monday = monday + timedelta(days=7)
        first_lesson, next_lesson, other_group = (f"/staff/schedule/{seed_id(key)}" for key in ("a1", "a5", "b1"))
        status, _, current = staff.request(f"/staff?date={self.today}&status=submitted&service=consultation")
        self.assertEqual(status, 200)
        self.assertIn(first_lesson, current)
        self.assertNotIn(next_lesson, current)
        self.assertNotIn(other_group, current)
        self.assertIn(f'date={next_monday}&amp;status=submitted&amp;service=consultation#staff-schedule', current)
        self.assertIn(f'type="date" name="date" value="{self.today}"', current)

        status, _, following = staff.request(f"/staff?date={next_monday}")
        self.assertEqual(status, 200)
        self.assertNotIn(first_lesson, following)
        self.assertIn(next_lesson, following)
        self.assertIn(f'href="/staff?date={self.today + timedelta(days=7)}#staff-schedule"', staff.request(next_lesson)[2])
        self.assertIn("На этой неделе занятий нет.", staff.request(f"/staff?date={monday - timedelta(days=7)}")[2])
        self.assertEqual(staff.request("/staff?date=not-a-date")[0], 422)

    def test_exam_reminder_and_read_status(self):
        a = self.client("student-a")
        self.assertNotIn("Приближается экзамен", a.request("/")[2])
        for path in ("/notifications", "/notifications?filter=unread"):
            self.assertIn("Приближается экзамен", a.request(path)[2])
        self.assertEqual(self.db_one("SELECT COUNT(*) FROM notification WHERE kind = 'exam'"), 1)
        notice = self.db_one("SELECT id FROM notification WHERE kind = 'exam'")
        path = f"/notifications/{notice}/read"
        fields = a.fields("/notifications", path)
        self.assertEqual(a.request(path, fields)[0], 303)
        self.assertEqual(a.request(path, fields)[0], 303)
        self.assertNotIn("Приближается экзамен", a.request("/notifications?filter=unread")[2])
        self.assertIn("Приближается экзамен", a.request("/notifications")[2])

    def test_concurrent_application_retries_create_one_application(self):
        student = self.client("student-a")
        path = "/services/consultation"
        fields = student.fields(path, path)
        fields.update(details="Подать один раз из двух вкладок.", student_id=seed_id("student-b"), role="staff")

        def send(_):
            client = Client(self.app)
            client.cookies = student.cookies.copy()
            status, headers, _ = client.request(path, fields)
            return status, headers["Location"]

        with ThreadPoolExecutor(max_workers=2) as pool:
            replies = list(pool.map(send, range(2)))
        self.assertEqual(len(set(replies)), 1)
        self.assertEqual(replies[0][0], 303)
        owner = self.db_one("SELECT student_id FROM application WHERE details = ?", (fields["details"],))
        self.assertEqual(owner, seed_id("student-a"))
        self.assertEqual(self.db_one("SELECT COUNT(*) FROM application WHERE details = ?", (fields["details"],)), 1)

    def test_university_scope_with_second_tenant_fixture(self):
        other = uuid4().hex
        with closing(connect(self.path)) as db:
            db.execute("INSERT INTO university VALUES ('test-other', 'Демо 2', 'Europe/Moscow', 'demo_university')")
            db.execute("""
                INSERT INTO program SELECT 'test-other', id, faculty, name
                FROM program WHERE university_id = ? AND id = 'ai'
            """, (UNIVERSITY,))
            db.execute("""
                INSERT INTO study_group SELECT 'test-other', id, name, program_id, course_year
                FROM study_group WHERE university_id = ? AND id = 'demo-ai'
            """, (UNIVERSITY,))
            db.execute("""
                INSERT INTO academic_period SELECT 'test-other', id, label, starts_on, ends_on
                FROM academic_period WHERE university_id = ?
            """, (UNIVERSITY,))
            db.execute("""
                INSERT INTO account (id, username, password_hash, full_name, role, university_id, group_id, period_id)
                SELECT ?, 'other-tenant', password_hash, 'Демо Студент', 'student',
                       'test-other', group_id, period_id FROM account WHERE username = 'student-a'
            """, (other,))
        client = self.client()
        self.assertEqual(client.login("other-tenant", self.passwords["student-a"])[0], 303)
        self.assertEqual(client.request(f"/schedule/{seed_id('a1')}")[0], 404)
        self.assertEqual(client.request(f"/announcements/{seed_id('welcome')}")[0], 404)
        self.assertEqual(client.request(f"/grades/{seed_id(f'student-a:algorithms:{self.period}')}")[0], 404)
        self.assertNotIn("Алгоритмы и структуры данных", client.request("/")[2])
        self.assertNotIn("Консультация деканата", client.request("/requests")[2])

    def test_security_headers_and_https_origin_policy(self):
        origin = "https://dean.example.test"
        app = create_app(self.path, WebConfig(origin, trust_proxy=True))
        client = Client(app)
        https = {"HTTP_HOST": "dean.example.test", "wsgi.url_scheme": "https"}
        status, headers, body = client.request("/healthz", **https)
        self.assertEqual((status, body), (200, "ok\n"))
        self.assertEqual(headers["Strict-Transport-Security"], "max-age=31536000")
        self.assertIn("default-src 'none'", headers["Content-Security-Policy"])
        self.assertIn("no-store", headers["Cache-Control"])
        for override in [{"wsgi.url_scheme": "http"}, {"HTTP_HOST": "other.example.test"}]:
            response = client.request("/login", **{**https, **override})
            self.assertEqual(response[0], 400)
            self.assertNotIn("Set-Cookie", response[1])
        self.assertEqual(client.request("/login", HTTP_HOST="dean.example.test",
                                        HTTP_X_FORWARDED_PROTO="https")[0], 400)
        response = client.request("/login", **https)
        self.assertEqual(response[1]["Referrer-Policy"], "same-origin")
        cookies = [value for key, value in client.last_headers if key == "Set-Cookie"]
        self.assertTrue(any(x.startswith("__Host-dean_login=") and "; Secure" in x for x in cookies))
        fields = Forms(response[2]).forms["/login"]
        fields.update(username="student-a", password=self.passwords["student-a"])
        good = client.request("/login", fields, HTTP_ORIGIN=origin, HTTP_X_REAL_IP="192.0.2.20", **https)
        self.assertEqual(good[0], 303)
        cookies = [value for key, value in client.last_headers if key == "Set-Cookie"]
        self.assertTrue(any(x.startswith("__Host-dean_session=") and "; Secure" in x
                            and "HttpOnly" in x and "SameSite=Lax" in x for x in cookies))
        self.assertEqual(client.request("/profile", **https)[0], 200)

    def test_cross_origin_and_malformed_forms_are_rejected_before_write(self):
        a = self.client("student-a")
        path = "/services/consultation"
        fields = a.fields(path, path)
        fields["details"] = "Форма с допустимым текстом."
        for overrides in ({"HTTP_ORIGIN": "https://other.example.test"},
                          {"HTTP_ORIGIN": "null"}, {"HTTP_SEC_FETCH_SITE": "cross-site"}):
            self.assertEqual(a.request(path, fields, **overrides)[0], 403)
        body = urlencode(fields).encode()
        self.assertEqual(a.request(path, body + b"&details=repeated")[0], 400)
        self.assertEqual(a.request(path, body, CONTENT_LENGTH=str(len(body) + 1))[0], 400)
        self.assertEqual(a.request(path, body, CONTENT_LENGTH="32769")[0], 413)
        self.assertEqual(a.request(path, b"csrf=%FF")[0], 400)
        self.assertEqual(self.db_one("SELECT count(*) FROM application WHERE student_id=?",
                                     (seed_id("student-a"),)), 0)
        self.assertEqual(a.request(path, fields, HTTP_ORIGIN="http://127.0.0.1")[0], 303)
        temp = self.client()
        status, headers, _ = temp.request("/favicon.ico", REQUEST_METHOD="HEAD")
        self.assertEqual(status, 204)
        self.assertNotIn("Set-Cookie", headers)
        self.assertNotIn("Location", headers)
        self.assertNotIn("Content-Length", headers)
        login = temp.fields("/login", "/login")
        login.update(username="student-a", password=self.passwords["student-a"])
        self.assertEqual(temp.request("/login", login, HTTP_ORIGIN="https://other.example.test")[0], 403)
        self.assertEqual(temp.request("/login", login, HTTP_ORIGIN="null")[0], 403)

    def test_passwords_sessions_rate_limit_and_literal_text(self):
        a = self.client("student-a")
        stored = self.db_one("SELECT password_hash FROM account WHERE id=?", (seed_id("student-a"),))
        self.assertRegex(stored, r"^[0-9a-f]{32}\$[0-9a-f]{128}$")
        self.assertNotIn(self.passwords["student-a"], stored)
        self.assertEqual(self.db_one("SELECT count(DISTINCT password_hash) FROM account"), 3)
        cookie = a.cookies["dean_session"]
        digest = self.db_one("SELECT token_hash FROM session")
        self.assertRegex(digest, r"^[0-9a-f]{64}$")
        self.assertNotEqual(cookie, digest)
        url, _ = self.submit(a, "Лабораторная: <em>текст</em> и O'Reilly & «кавычки».")
        display = a.request(url)[2]
        self.assertIn("&lt;em&gt;текст&lt;/em&gt;", display)
        self.assertIn("O&#x27;Reilly &amp;", display)
        self.assertNotIn("<em>текст</em>", display)
        with closing(connect(self.path)) as db:
            db.execute("UPDATE session SET expires_at = '2000-01-01T00:00:00+00:00'")
            db.executemany("INSERT INTO login_attempt VALUES ('127.0.0.1', ?, ?)",
                           [("student-a", datetime.now(ZoneInfo("UTC")).isoformat())] * 6)
        self.assertEqual(a.request("/profile")[0], 303)
        self.assertEqual(self.client().login("student-a", "invalid")[0], 429)
        self.assertEqual(self.client().login("student-b", self.passwords["student-b"])[0], 303)

    def test_readiness_and_backup_of_database_without_plaintext_password_file(self):
        a = self.client("student-a")
        url, _ = self.submit(a)
        destination = Path(self.workspace.name) / "restore" / "snapshot.sqlite3"
        backup(self.path, destination)
        self.assertEqual(stat.S_IMODE(destination.stat().st_mode), 0o600)
        self.assertFalse(credentials_path(destination).exists())
        with closing(connect(destination)) as db:
            migrate(db)
            seed(db, destination, self.today)
            self.assertEqual(db.execute("PRAGMA user_version").fetchone()[0], schema_version())
            self.assertEqual(db.execute("PRAGMA foreign_key_check").fetchall(), [])
        restored = Client(create_app(destination))
        self.assertEqual(restored.login("student-a", self.passwords["student-a"])[0], 303)
        self.assertIn("Отправлена", restored.request(url)[2])
        self.assertEqual(restored.request("/healthz")[0], 200)
        missing = Path(self.workspace.name) / "missing.sqlite3"
        with self.assertLogs("dean.web", level="ERROR"):
            self.assertEqual(Client(create_app(missing)).request("/healthz")[0], 503)
        self.assertFalse(missing.exists())

    def test_migration_from_original_schema_and_private_seed(self):
        target = Path(self.workspace.name) / "imported.sqlite3"
        with closing(connect(target)) as db:
            first = ROOT / "dean" / "migrations" / "001_initial.sql"
            db.executescript(first.read_text(encoding="utf-8") + "\nPRAGMA user_version = 1;")
            seed(db, target, self.today, self.passwords)
            initial = db.execute("SELECT count(*) FROM account").fetchone()[0]
            migrate(db)
            migrate(db)
            self.assertEqual(db.execute("PRAGMA user_version").fetchone()[0], schema_version())
            self.assertEqual(db.execute("SELECT count(*) FROM account").fetchone()[0], initial)
        self.assertFalse(credentials_path(target).exists())
        self.assertEqual(stat.S_IMODE(target.stat().st_mode), 0o600)

    def test_state_survives_two_independent_http_worker_processes(self):
        a = self.client("student-a")
        action = "/services/consultation"
        fields = a.fields(action, action)
        fields["details"] = "Сохранение после остановки процесса приложения."
        before, (status, url, _) = self.http_worker(a, "POST", action, fields)
        self.assertEqual(status, 303)
        after, (status, _, html) = self.http_worker(a, "GET", url)
        self.assertNotEqual(before, after)
        self.assertEqual(status, 200)
        self.assertIn(fields["details"], html)
        self.assertIn("Отправлена", html)


if __name__ == "__main__":
    unittest.main()
