from contextlib import closing
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch

from dean import ai, lecture_library
from dean.access_config import AccessConfig
from dean.config import WebConfig
from dean.db import connect, migrate
from dean.web import create_app
from scripts import provision
from tests.test_dean import Client, DemoFixture, Forms


ROOT="/lecture-library/semantic-web"
WATCH=ROOT+"/intro"


class ModelFixture:
    enabled=True
    provider_name="DeepSeek"
    daily_limit=100
    total_limit=300

    def __init__(self):
        self.calls=[]

    def complete(self,messages,max_tokens,*,model):
        self.calls.append((messages,model))
        return {"notes":["04:11. Дата и место относятся к событию."]*4,
                "practice":["Укажи тип события.","Выдели дату.","Выдели место."]}


class CatalogTest(DemoFixture):
    def test_demo_header_shows_active_account_and_stale_form_cannot_cross_accounts(self):
        with patch.dict(os.environ, {"DEAN_PRODUCT": "1", "DEAN_BROWSER_DEMO": "0"}):
            first = self.client("student-a")
            second = self.client("student-b")
            form = first.fields("/tasks/new", "/tasks/new")
            self.assertIn('class="dean-demo-profile-login">student-a<', first.request("/")[2])
            self.assertIn('class="dean-demo-profile-login">student-b<', second.request("/")[2])
            title = "Личная задача исходного сеанса"
            response = second.request("/tasks/new", {**form, "title": title, "priority": "normal"})
            self.assertEqual(response[0], 403)
            for client in (first, second):
                self.assertNotIn(title, client.request("/tasks")[2])

    def test_product_uses_one_navigation_and_schedule_entry(self):
        with patch.dict(os.environ, {"DEAN_PRODUCT": "1"}):
            student = self.client("student-a")
            status, _, html = student.request("/")
            self.assertEqual(status, 200)
            self.assertEqual(html.count('id="dean-navigation"'), 1)
            self.assertIn('aria-controls="dean-navigation"', html)
            self.assertNotIn('class="rw-dock', html)
            rail = html.split('id="dean-navigation"', 1)[1].split("</aside>", 1)[0]
            for path in ("/schedule", "/requests", "/documents", "/bookings", "/tasks", "/lecture-library"):
                self.assertIn(f'href="{path}"', rail)
            self.assertNotIn('href="/schedule?view=list"', rail)
            schedule = student.request("/schedule")[2]
            self.assertIn("<h1>Расписание</h1>", schedule)
            self.assertIn('data-calendar-view="week" aria-current="page"', schedule)
            for view in ("day", "list", "month"):
                self.assertIn(f'data-calendar-view="{view}"', schedule)

    def test_published_demo_exposes_pass_in_catalog_and_search(self):
        with patch.dict(os.environ, {"DEAN_PRODUCT": "1", "DEAN_BROWSER_DEMO": "0"}):
            student = self.client("student-b")
            for path in ("/all-services?q=пропуск", "/search?q=пропуск"):
                self.assertIn('href="/campus-pass"', student.request(path)[2])
            status, _, html = student.request("/campus-pass")
            self.assertEqual(status, 200)
            self.assertIn("Код для демонстрации", html)
            self.assertEqual(self.client("staff").request("/campus-pass")[0], 404)

    def test_course_quiz_video_favorite_progress_and_ai_are_private(self):
        engine=ModelFixture()
        app=create_app(self.path,ai_engine=engine)
        primary,other=Client(app),Client(app)
        self.assertEqual(Client(app).request(WATCH)[1]["Location"],"/login?next=%2Flecture-library%2Fsemantic-web%2Fintro")
        for client, login in ((primary,"student-a"),(other,"student-b")):
            self.assertEqual(client.login(login,self.passwords[login])[0],303)
        rendered=primary.request(WATCH)
        self.assertContains(rendered,"Русский перевод")
        self.assertContains(rendered,"Микроформаты и RDFa")
        favorite=primary.fields(ROOT,ROOT+"/favorite")
        self.assertEqual(primary.request(ROOT+"/favorite",favorite)[0],303)
        self.assertContains(primary.request("/lecture-library?mine=1"),"Введение в семантический веб")
        self.assertContains(other.request("/lecture-library?mine=1"),"Курсы не найдены")
        self.assertContains(primary.request("/lecture-library?q=RDFa"),"Введение в семантический веб")

        _,headers,data=primary.request(WATCH+"/captions/ru")
        self.assertTrue(headers["Content-Type"].startswith("text/vtt"))
        self.assertTrue(data.startswith("WEBVTT"))
        # The fixture client decodes bodies as UTF-8. Test the streamed bytes directly.
        _,lesson=lecture_library.selected("semantic-web","intro")
        from dean.lecture_studio import MediaBody,range_bounds
        offset,end=range_bounds(lesson["video"].stat().st_size,"bytes=2-9")
        self.assertEqual(b"".join(MediaBody(lesson["video"],offset,end)),lesson["video"].read_bytes()[2:10])
        self.assertEqual(primary.request(WATCH+"/video",HTTP_RANGE="bytes=900000000-")[0],416)
        head=primary.request(WATCH+"/video",REQUEST_METHOD="HEAD")
        self.assertEqual(head[0],200)
        self.assertGreater(int(head[1]["Content-Length"]),1000000)

        forms=Forms(rendered[2]).forms
        key=forms[WATCH+"/materials"]
        primary.request(WATCH+"/materials",key)
        self.assertEqual(primary.request(WATCH+"/materials",key)[0],303)
        self.assertEqual(len(engine.calls),1)
        self.assertEqual(engine.calls[0][1],ai.CHAT_MODEL)
        self.assertIn("Всемирной паутины",engine.calls[0][0][1]["content"])
        self.assertContains(primary.request(WATCH),"Мой конспект DeepSeek")
        self.assertNotIn("Мой конспект DeepSeek",other.request(WATCH)[2])

        payload={"csrf":key["csrf"],"question":"0","answer":"0"}
        self.assertEqual(primary.request(WATCH+"/quiz",payload)[0],303)
        self.assertContains(primary.request(WATCH),"Верно.")
        self.assertNotIn(">Верно.",other.request(WATCH)[2])
        self.assertEqual(primary.request(WATCH+"/progress",{"csrf":key["csrf"],"seconds":"221"})[0],204)
        self.assertContains(primary.request(ROOT),"Продолжить с 3:41")
        self.assertNotIn("Продолжить с",other.request(ROOT)[2])

    def assertContains(self,response,expected):
        self.assertEqual(response[0],200)
        self.assertIn(expected,response[2])


class ProductTest(TestCase):
    def test_installation_without_demo_data_and_https_design(self):
        with TemporaryDirectory() as tmp, patch.dict("os.environ", {
            "DEAN_PRODUCT": "1", "DEAN_CAMPUS_LAB": "1", "DEAN_EXT_CAMPUS_DIRECTORY": "1",
            "DEAN_EXT_ROOM_OCCUPANCY": "0", "DEAN_ROOM_OCCUPANCY_CAMERA": "0",
        }):
            path=Path(tmp)/"dean.sqlite3"
            with closing(connect(path)) as db:
                migrate(db)
                site=json.loads(Path("deploy/site.example.json").read_text())
                site["contacts"]=[{"kind":"department","name":"Тестовый контакт","description":"Консультация","office":"Кабинет 1012"}]
                uid=provision.register_site(db,site)
                self.assertEqual(provision.register_site(db,site),uid)
                account=provision.register_account(db,uid,"review.one","Учебный пользователь","student","review-example-password",
                                                    ["group-01"],"autumn-2026")
                self.assertEqual(db.execute("SELECT id FROM account").fetchone()[0],account)
                self.assertEqual(db.execute("SELECT account_id FROM personal_login").fetchone()[0],account)
            env={"HTTP_HOST":"dean.example.org","wsgi.url_scheme":"https","HTTP_ORIGIN":"https://dean.example.org"}
            client=Client(create_app(path,WebConfig("https://dean.example.org"),access=AccessConfig(vk_client="12345",yandex_client="example",yandex_secret="fixture")))
            code,headers,html=client.request("/login",**env)
            self.assertEqual(code,200)
            self.assertIn("Вход в деканат",html)
            self.assertEqual(html.count("<h1>"), 1)
            self.assertIn('data-theme="raspay"', html)
            self.assertIn('data-vishka aria-hidden="true"', html)
            self.assertIn('class="vishka-front"', html)
            self.assertIn('class="vishka-back"', html)
            self.assertIn('/static/login-mascot.js?', html)
            self.assertNotIn("campus-atrium-login",html)
            self.assertNotIn("Светлый кабинет",html)
            self.assertNotIn("Цифровой деканат МГИМО",html)
            self.assertIn("/auth/yandex/start",html)
            self.assertIn("/auth/vk/start",html)
            self.assertIn("Secure",str(client.last_headers))
            self.assertEqual(client.request("/demo",**env)[0],303)
            form=Forms(html).forms["/login"]
            self.assertEqual(client.request("/login",form|{"username":"review.one","password":"review-example-password"},**env)[0],303)
            self.assertEqual(client.request("/campus-directory",**env)[0],200)
            self.assertIn("Тестовый контакт",client.request("/campus-directory",**env)[2])
            with patch.dict(os.environ, {"DEAN_CAMPUS_LAB": "0"}):
                login = Forms(client.request("/login", **env)[2]).forms["/login"]
                self.assertEqual(client.request("/login", login | {
                    "username": "review.one", "password": "review-example-password",
                }, **env)[0], 303)
                status, _, directory = client.request("/campus-directory", **env)
                self.assertEqual(status, 200)
                self.assertNotIn('href="/campus-lab"', directory)
                self.assertIn('href="/campus-directory/contacts/1"', directory)
                status, _, contact = client.request("/campus-directory/contacts/1", **env)
                self.assertEqual(status, 200)
                self.assertIn("Кабинет 1012", contact)
            self.assertEqual(client.request("/lecture-library",**env)[0],200)
            services=client.request("/all-services",**env)
            self.assertEqual(services[0],200)
            self.assertIn("Бронирования",services[2])
            self.assertIn('class="dean-service-board"', services[2])
            self.assertIn('href="/campus-occupancy"', services[2])
            booking_page = client.request("/bookings", **env)[2]
            self.assertIn('href="/campus-occupancy" class="dean-secondary-action"', booking_page)
            occupancy = client.request("/campus-occupancy", **env)
            self.assertEqual(occupancy[0], 200)
            self.assertEqual(occupancy[2].count('data-zone="'), 27)
            self.assertNotIn("data-door-camera", occupancy[2])
            self.assertNotIn("Открыть кампус", services[2])
            self.assertNotIn("booking-room.jpg",services[2])
            self.assertNotIn("Светлый кабинет",services[2])
            matches = client.request("/all-services?q=%D0%BB%D0%B5%D0%BA%D1%86%D0%B8%D0%B8", **env)[2]
            self.assertIn("Лекции", matches)
            self.assertNotIn("Заявки и документы", matches)
            self.assertIn("Сервисы не найдены", client.request("/all-services?q=xyzxyz", **env)[2])
            self.assertEqual(client.request("/campus-lab",**env)[0],200)

    def test_deepseek_posts_json_to_official_host_and_has_separate_vision(self):
        captured={}
        class Connection:
            status=200

            def __init__(self, host, port, timeout): captured.update(host=host,port=port)
            def request(self,method,path,body,headers): captured.update(method=method,path=path,data=json.loads(body))
            def getresponse(self): return self
            def __enter__(self): return self
            def __exit__(self,*args): return False
            def read(self,n): return b'{"choices":[{"message":{"content":"{\\"result\\":7}"}}]}'
            def close(self): pass
        engine=ai.RouterAI("fixture-key",connect=Connection,provider_name="DeepSeek")
        self.assertFalse(engine.vision_enabled)
        self.assertEqual(engine.complete([{"role":"user","content":"Return JSON."}],50,model=ai.CHAT_MODEL),{"result":7})
        self.assertEqual(captured["host"],"api.deepseek.com")
        self.assertEqual(captured["path"],"/chat/completions")
        self.assertEqual(captured["data"]["thinking"],{"type":"disabled"})
        self.assertEqual(captured["data"]["response_format"],{"type":"json_object"})
