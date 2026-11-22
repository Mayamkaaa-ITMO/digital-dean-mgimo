import json
from io import BytesIO
import random
import re
import shutil
import struct
import wave
import zlib
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from datetime import timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Event
from unittest import TestCase, skipUnless
from unittest.mock import patch
from urllib.parse import urlencode, urlsplit

from dean.ai import CHAT_MODEL, PHOTO_MODEL, RouterAI
from dean import assistant_voice
from dean.attachment import PNG, png_chunk
from dean.clock import at, now
from dean.db import connect
from dean.domain import DomainError
from dean.reservations import fixture_id
from dean.seed import UNIVERSITY
from dean.web import create_app

from .test_dean import Client, DemoFixture, Forms


PHOTO = "/calendar/import/photo"
PIXEL = (PNG + png_chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0))
         + png_chunk(b"IDAT", zlib.compress(b"\x00\xff\xff\xff")) + png_chunk(b"IEND", b""))


class Model:
    enabled = True
    daily_limit = 100
    total_limit = 300

    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = []

    def complete(self, messages, max_tokens, *, model):
        self.calls.append((messages, max_tokens, model))
        return self.responses.pop(0)


def upload(client, path, fields, payload, *, filename="test.png"):
    boundary = "DeansPhotoBoundary"
    parts = [f'--{boundary}\r\nContent-Disposition: form-data; name="{key}"\r\n\r\n{value}\r\n'.encode()
             for key,value in fields.items() if key != "file"]
    parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="{filename}"\r\n'
                 'Content-Type: image/png\r\n\r\n'.encode())
    parts.extend((payload, f"\r\n--{boundary}--\r\n".encode()))
    return client.request(path,b"".join(parts),CONTENT_TYPE=f"multipart/form-data; boundary={boundary}")


class RouterAIWireTest(TestCase):
    def test_model_requests_use_configured_route_and_errors_are_sanitized(self):
        calls = []
        messages = [{"role":"user","content":[{"type":"image_url","image_url":{"url":"data:image/png;base64,eA=="}}]}]

        class Connection:
            status = 200

            def request(self, method, path, body, headers):
                calls.append((method,path,json.loads(body),headers))

            def getresponse(self):
                return self

            def __enter__(self):
                return self

            def __exit__(self,*args):
                return False

            def read(self,_limit):
                return b'{"choices":[{"message":{"content":"{\\"events\\":[]}"}}]}'

            def close(self):
                pass

        conn = Connection()
        config = RouterAI("fixture-key",connect=lambda host, port, **kwargs: calls.append((host,port)) or conn)
        self.assertEqual(config.complete(messages,3200),{"events":[]})
        self.assertEqual(calls[0],("routerai.ru",443))
        method,path,wire,headers = calls[1]
        self.assertEqual((method,path),("POST","/api/v1/chat/completions"))
        self.assertEqual(wire["model"],PHOTO_MODEL)
        self.assertNotIn("provider", wire)
        self.assertEqual(wire["max_tokens"],3200)
        self.assertEqual(wire["messages"],messages)
        self.assertEqual(headers["Authorization"],"Bearer fixture-key")
        chat = [{"role":"user","content":"Где скачать готовую справку?"}]
        config.complete(chat,420,model=CHAT_MODEL)
        _,_,wire,_ = calls[-1]
        self.assertEqual(wire["model"],"deepseek/deepseek-v4.1-flash-1017")
        self.assertEqual(wire["provider"],{"only":["deepinfra"],"allow_fallbacks":False})
        self.assertEqual(wire["messages"],chat)
        self.assertEqual(wire["response_format"],{"type":"json_object"})
        conn.status = 429
        with self.assertRaises(DomainError) as caught:
            config.complete(messages,3200)
        self.assertEqual(caught.exception.status,503)
        self.assertNotIn("fixture-key",caught.exception.message)

    def test_reads_local_key_file_and_prefers_explicit_environment_key(self):
        with TemporaryDirectory() as directory, patch.dict("os.environ", {}, clear=True):
            key_file = Path(directory) / "routerai.key"
            key_file.write_text("file-fixture-key\n", encoding="ascii")
            with patch.dict("os.environ", {"DEAN_AI_API_KEY_FILE": str(key_file)}):
                config = RouterAI.from_env()
                self.assertEqual(config.api_key,"file-fixture-key")
                self.assertNotIn("file-fixture-key",repr(config))
                key_file.unlink()
                with self.assertRaisesRegex(ValueError,"DEAN_AI_API_KEY_FILE"):
                    RouterAI.from_env()
                with patch.dict("os.environ", {"DEAN_AI_API_KEY": "env-fixture-key"}):
                    self.assertEqual(RouterAI.from_env().api_key,"env-fixture-key")


class AssistedDeanTest(DemoFixture):
    def model(self, *responses):
        engine = Model(*responses)
        self.app = create_app(self.path, ai_engine=engine)
        return engine

    @skipUnless(shutil.which("ffmpeg"), "ffmpeg is required to decode the voice fixture")
    def test_dictation_uses_authenticated_draft_and_bounds_upload(self):
        engine = self.model()
        student = self.client("student-a")
        fields = student.fields("/assistant", "/assistant")
        path = assistant_voice.PATH
        sample = BytesIO()
        with wave.open(sample, "wb") as wav:
            wav.setparams((1, 2, 16000, 0, "NONE", "not compressed"))
            wav.writeframes(b"\x00\x00" * 6000)
        audio = sample.getvalue()
        with patch.object(assistant_voice, "local_model", return_value="fixture"), \
             patch("dean.lecture_analysis.whisper_text", return_value="[Шум] Когда следующая пара?") as whisper:
            self.assertIn(f'data-dean-dictation="{path}"', student.request("/assistant")[2])
            status, _, reply = upload(student, path, {"csrf": fields["csrf"]}, audio)
            self.assertEqual(status, 200)
            self.assertEqual(json.loads(reply), {"text": "Когда следующая пара?"})
            self.assertEqual(upload(student, path, {"csrf": "invalid"}, audio)[0], 403)
            self.assertEqual(upload(Client(self.app), path, {"csrf": fields["csrf"]}, audio)[0], 403)
            self.assertEqual(upload(student, path, {"csrf": fields["csrf"]}, b"junk")[0], 422)
            self.assertEqual(upload(student, path, {"csrf": fields["csrf"]},
                                    b"\0" * (assistant_voice.MAX_BYTES + 1))[0], 413)
            whisper.assert_called_once()
        self.assertFalse(engine.calls)

    @patch.dict("os.environ", {"DEAN_EXT_PHOTO_IMPORT": "0"})
    def test_photo_preview_private_confirmation_and_idempotency(self):
        event = {"day":1,"start":"08:50","end":"10:25","subject":"Демо-анализ фото","room":"А-301",
                 "kind":"семинар","uncertain":False}
        engine = self.model({"events":[event]}, {"events":[event]})
        a,b,staff = (self.client(name) for name in ("student-a","student-b","staff"))
        first = self.today - timedelta(days=self.today.weekday())
        fields = {**a.fields(PHOTO,PHOTO),"week":first.isoformat()}
        before = self.db_one("SELECT count(*) FROM personal_event")
        response = upload(a, PHOTO, fields, PIXEL, filename="Снимок расписания.png")
        self.assertEqual(response[0],303)
        draft = response[1]["Location"]
        self.assertEqual(upload(a, PHOTO, fields, PIXEL)[1]["Location"],draft)
        self.assertEqual(len(engine.calls),1)
        self.assertEqual(engine.calls[0][2],PHOTO_MODEL)
        self.assertEqual(b.request(draft)[0],404)
        self.assertEqual(upload(staff,PHOTO,{**fields,"csrf":staff.fields("/staff","/logout")["csrf"]},PIXEL)[0],403)
        self.assertEqual(self.db_one("SELECT count(*) FROM personal_event"),before)
        confirm = draft+"/confirm"
        self.assertIn(event["subject"],a.request(draft)[2])
        self.assertEqual(a.request(confirm,a.fields(draft,confirm))[0],303)
        self.assertEqual(self.db_one("SELECT count(*) FROM personal_event"),before+1)
        another = upload(a,PHOTO,{**a.fields(PHOTO,PHOTO),"week":first.isoformat()},PIXEL)[1]["Location"]
        self.assertEqual(a.request(another+"/confirm",a.fields(another,another+"/confirm"))[0],303)
        self.assertEqual(self.db_one("SELECT count(*) FROM personal_event"),before+1)

    @patch.dict("os.environ", {"DEAN_EXT_PHOTO_IMPORT": "0"})
    def test_invalid_image_and_unknown_rows_stay_out_of_calendar(self):
        engine = self.model({"events":[{"day":None,"subject":"Требует сверки","uncertain":True}]})
        a = self.client("student-a")
        form = {**a.fields(PHOTO,PHOTO),"week":self.today.isoformat()}
        self.assertEqual(upload(a,PHOTO,form,b"arbitrary bytes")[0],422)
        self.assertFalse(engine.calls)
        draft = upload(a,PHOTO,form,PIXEL)[1]["Location"]
        self.assertIn("Сверь день, название и время",a.request(draft)[2])
        self.assertNotIn("Подтвердить личный импорт",a.request(draft)[2])

    def test_tomorrows_lessons_are_available_without_the_model(self):
        self.app = create_app(self.path, ai_engine=RouterAI())
        student = self.client("student-a")
        path = "/assistant"
        fields = {**student.fields(path, path), "message": "Какие пары завтра?"}
        status, _, reply = student.request(path, fields, HTTP_ACCEPT="application/json")
        self.assertEqual(status, 200)
        response = json.loads(reply)
        self.assertIn("Иностранный язык", response["text"])
        day = (self.today + timedelta(days=1)).isoformat()
        self.assertIn("/schedule?date=" + day + "&view=day", response["link"])

    def test_profile_photo_upload_and_removal_are_owner_scoped(self):
        route = "/profile/photo"
        student, other = (self.client(username) for username in ("student-a", "student-b"))
        fields = student.fields("/profile", route)
        self.assertEqual(upload(student, route, fields, b"not an image")[0], 422)
        self.assertEqual(upload(student, route, fields, PIXEL)[0], 303)
        self.assertIn('class="dean-header-photo"', student.request("/")[2])
        self.assertNotIn('class="dean-header-photo"', other.request("/")[2])
        status, headers, _ = student.request(route)
        self.assertEqual((status, headers["Content-Type"]), (200, "image/png"))
        self.assertEqual(other.request(route)[0], 404)
        self.assertEqual(student.request(route + "/remove", student.fields("/profile", route + "/remove"))[0], 303)
        self.assertEqual(student.request(route)[0], 404)
        self.assertNotIn('class="dean-header-photo"', student.request("/")[2])

    @skipUnless(shutil.which("ffmpeg"), "FFmpeg is required to normalize a large profile image")
    def test_large_profile_photo_is_resized_and_oversize_error_stays_in_profile(self):
        route = "/profile/photo"
        student = self.client("student-a")
        fields = student.fields("/profile", route)
        rng = random.Random(15)
        pixels = b"".join(b"\x00" + rng.randbytes(1200 * 3) for _ in range(1200))
        photo = (PNG + png_chunk(b"IHDR", struct.pack(">IIBBBBB", 1200, 1200, 8, 2, 0, 0, 0))
                 + png_chunk(b"IDAT", zlib.compress(pixels)) + png_chunk(b"IEND", b""))
        self.assertGreater(len(photo), 3 * 1024**2)
        self.assertEqual(upload(student, route, fields, photo)[0], 303)
        status, _, body = student.request(route)
        self.assertEqual(status, 200)
        self.assertEqual(struct.unpack_from(">II", body, 16), (512, 512))
        status, _, page = upload(student, route, fields, b"0" * (21 * 1024**2))
        self.assertEqual(status, 413)
        self.assertIn("Фото профиля должно быть не больше 20 МиБ", page)
        self.assertIn('action="/profile/photo"', page)
        self.assertEqual(student.request(route)[2], body)

    def test_assistant_stays_in_the_cabinet_without_blocking_material_questions(self):
        self.model({"action": "guide", "page": "home", "answer": "def bubble_sort(values): pass"},
                   {"action": "guide", "page": "photo", "answer": "Загрузи снимок и проверь пары в черновике."},
                   {"action": "off_topic"})
        student = self.client("student-a")
        path = "/assistant"
        for question, expected in (("Напиши сортировку пузырьком на питоне", "Могу помочь"),
                                   ("Куда загрузить фотографию моего расписания?", "проверь пары"),
                                   ("Какая погода будет завтра?", "Могу помочь")):
            response = student.request(path, {**student.fields(path, path), "message": question},
                                       HTTP_ACCEPT="application/json")
            self.assertEqual(response[0], 200)
            answer = json.loads(response[2])["text"]
            self.assertIn(expected, answer)
            self.assertNotIn("bubble_sort", answer)

    def test_assistant_navigates_and_confirms_owner_scoped_actions(self):
        engine = self.model({"action":"create_task","title":"Подготовить стенд"},
                            {"action":"draft_certificate","purpose":"Для конкурса"})
        a,b,staff = (self.client(name) for name in ("student-a","student-b","staff"))
        route = "/assistant"
        accept = {"HTTP_ACCEPT":"application/json"}
        ask = lambda client, message: {**client.fields(route,route),"message":message}
        opened = a.request(route,ask(a,"Открой расписание"))
        self.assertEqual((opened[0],opened[1]["Location"]),(303,"/schedule"))
        self.assertEqual(len(engine.calls),0)
        self.assertEqual(a.request(route,ask(a,"Расписание сегодня"))[0],200)
        self.assertEqual(staff.request(route,ask(staff,"Очередь заявок"))[0],200)
        payload = ask(a,"Мне нужно подготовить стенд, занеси в список")
        result = json.loads(a.request(route,payload,**accept)[2])
        self.assertEqual(json.loads(a.request(route,payload,**accept)[2])["proposal"],result["proposal"])
        self.assertEqual(len(engine.calls),1)
        self.assertEqual(self.db_one("SELECT count(*) FROM task WHERE title='Подготовить стенд'"),0)
        action = "/assistant/actions/" + result["proposal"] + "/confirm"
        csrf = lambda client: {"csrf":client.fields(route,route)["csrf"]}
        self.assertEqual(b.request(action,csrf(b))[0],404)
        self.assertEqual(staff.request(action,csrf(staff))[0],403)
        saved = a.request(action,csrf(a))
        self.assertEqual(saved[0],303)
        self.assertEqual(a.request(action,csrf(a))[1]["Location"],saved[1]["Location"])
        self.assertEqual(self.db_one("SELECT count(*) FROM task WHERE title='Подготовить стенд'"),1)
        result = json.loads(a.request(route,ask(a,"Отметь задачу Подготовить стенд выполненной"),**accept)[2])
        done = "/assistant/actions/" + result["proposal"] + "/confirm"
        self.assertEqual(a.request(done,csrf(a))[0],303)
        self.assertEqual(self.db_one("SELECT state FROM task WHERE title='Подготовить стенд'"),"done")
        result = json.loads(a.request(route,ask(a,"Подготовь черновик справки для конкурса"),**accept)[2])
        draft = a.request("/assistant/actions/" + result["proposal"] + "/confirm", csrf(a))
        self.assertEqual(draft[0],303)
        self.assertEqual(self.db_one("SELECT status FROM application WHERE id=?",
                                     (draft[1]["Location"].rsplit("/",1)[1],)), "draft")

    def test_assistant_rejects_model_supplied_url_and_enforces_budget(self):
        engine = self.model({"action":"navigate","page":"//malicious.invalid"})
        engine.daily_limit = 1
        a = self.client("student-a")
        route = "/assistant"
        accept = {"HTTP_ACCEPT":"application/json"}
        first = a.request(route,{**a.fields(route,route),"message":"Открой скрытую страницу"},**accept)
        self.assertEqual(first[0],200)
        self.assertNotIn("navigate",json.loads(first[2]))
        self.assertEqual(a.request(route,{**a.fields(route,route),"message":"Что можете сделать?"},**accept)[0],429)
        self.assertEqual(a.request(route,{**a.fields(route,route),"message":"Мои задачи"},**accept)[0],200)
        self.assertEqual(len(engine.calls),1)

    def test_assistant_books_a_real_room_after_confirmation(self):
        self.app = create_app(self.path, ai_engine=RouterAI())
        student, other, staff = (self.client(name) for name in ("student-a", "student-b", "staff"))
        room = fixture_id(UNIVERSITY, "room-focus")
        with closing(connect(self.path)) as db:
            db.execute("UPDATE bookable SET title=? WHERE id=?", ("Коворкинг «Маяк»", room))
        chat = "/assistant"
        first = {**student.fields(chat, chat), "message": "Забронируй мне коворкинг"}
        status, _, body = student.request(chat, first, HTTP_ACCEPT="application/json")
        self.assertEqual(status, 200)
        self.assertIn("На какую дату", json.loads(body)["text"])
        payload = {**student.fields(chat, chat), "message": "Завтра"}
        status, _, body = student.request(chat, payload, HTTP_ACCEPT="application/json")
        self.assertEqual(status, 200)
        reply = json.loads(body)
        self.assertIn("Коворкинг «Маяк»", reply["caption"])
        self.assertIn("Забронировать", reply["turn_html"])
        sid = fixture_id(UNIVERSITY, "room-focus:0")
        count = lambda: self.db_one("SELECT count(*) FROM reservation WHERE slot_id=?", (sid,))
        self.assertEqual(count(), 0)
        action = "/assistant/actions/" + reply["proposal"] + "/confirm"
        self.assertEqual(other.request(action, {"csrf": other.fields(chat, chat)["csrf"]})[0], 404)
        form = student.fields(chat, action)
        status, headers, _ = student.request(action, form)
        self.assertEqual(status, 303)
        self.assertTrue(headers["Location"].startswith("/bookings/"))
        self.assertEqual(student.request(action, form)[1]["Location"], headers["Location"])
        self.assertEqual(count(), 1)
        self.assertIn("Коворкинг «Маяк»", student.request(headers["Location"])[2])
        self.assertIn("Открыть бронь", student.request(chat)[2])
        self.assertIn("Мира Яроветова", staff.request("/staff/slots/" + sid)[2])

    def test_assistant_booking_rechecks_room_and_personal_calendar_at_confirmation(self):
        self.app = create_app(self.path, ai_engine=RouterAI())
        student = self.client("student-a")
        chat = "/assistant"
        fields = {**student.fields(chat, chat), "message": "Забронируй переговорную «Фокус» на завтра с 10:00 до 11:00"}
        reply = json.loads(student.request(chat, fields, HTTP_ACCEPT="application/json")[2])
        self.assertIn("10:00–11:00", reply["caption"])
        booking = "/assistant/actions/" + reply["proposal"] + "/confirm"
        day = (self.today + timedelta(days=1)).isoformat()
        sid = fixture_id(UNIVERSITY, "room-focus:0")
        form = student.fields("/events/new", "/events/new")
        event = {**form, "title": "Личное занятие", "starts": f"{day}T10:30", "ends": f"{day}T11:00"}
        self.assertEqual(student.request("/events/new", event)[0], 303)
        self.assertEqual(student.request(booking, student.fields(chat, booking))[0], 409)
        self.assertEqual(self.db_one("SELECT count(*) FROM reservation WHERE slot_id=?", (sid,)), 0)

    def test_assistant_reuses_pending_request_after_interrupted_response(self):
        entered, finish = Event(), Event()
        engine = self.model({"action":"create_task","title":"Проверить стенд"})
        answer = engine.complete

        def delayed(*args, **kwargs):
            entered.set()
            if not finish.wait(8):
                raise AssertionError("Ответ тестовой модели не был разблокирован")
            return answer(*args, **kwargs)

        engine.complete = delayed
        student = self.client("student-a")
        retry = Client(self.app)
        retry.cookies.update(student.cookies)
        route = "/assistant"
        fields = {**student.fields(route,route),"message":"Добавь в список дел проверку стенда сегодня к 19:00",
                  "previous":"Какие задачи у меня есть на сегодня?"}
        headers = {"HTTP_ACCEPT":"application/json"}
        with ThreadPoolExecutor(max_workers=1) as pool:
            initial = pool.submit(student.request,route,fields,**headers)
            try:
                self.assertTrue(entered.wait(5))
                status,_,body = retry.request(route,fields,**headers)
                self.assertEqual(status,409)
                self.assertEqual(json.loads(body)["next_id"],fields["request_id"])
                status,_,html = retry.request(route,fields)
                self.assertEqual(status,409)
                self.assertIn(fields["message"],html)
                form = Forms(html).forms[route]
                self.assertEqual(form["request_id"],fields["request_id"])
                self.assertEqual(form["previous"],fields["previous"])
            finally:
                finish.set()
            status,_,original = initial.result(timeout=5)
            self.assertEqual(status,200)
        status,_,result = retry.request(route,fields,**headers)
        self.assertEqual(status,200)
        self.assertEqual(json.loads(result)["proposal"],json.loads(original)["proposal"])
        self.assertEqual(self.db_one("SELECT reserved FROM ai_budget"),1)
        self.assertEqual(self.db_one("SELECT COUNT(*) FROM assistant_proposal"),1)
        self.assertEqual(len(engine.calls),1)

    def test_dashboard_chat_uses_role_guide_and_opens_registered_page(self):
        engine = self.model(
            {"action": "guide", "page": "documents", "answer": "Готовый файл доступен в «Моих документах»."},
            {"action": "guide", "page": "home", "answer": "Посмотри очередь своих групп."},
        )
        student, staff = (self.client(name) for name in ("student-a", "staff"))
        for client, path in ((student, "/"), (staff, "/staff")):
            dashboard = client.request(path)[2]
            self.assertIn('id="dashboard-assistant"', dashboard)
            for markup in (dashboard, client.request("/assistant")[2]):
                self.assertNotIn("data-chat-voice-", markup)
                self.assertIn('name="message"', markup)
                self.assertIn('aria-label="Отправить"', markup)
            fields = {**client.fields(path, "/assistant"), "message": "Подскажи, где посмотреть результат обращения"}
            result = json.loads(client.request("/assistant", fields, HTTP_ACCEPT="application/json")[2])
            self.assertIn("text", result)
            if path == "/":
                self.assertEqual(result["link"], "/documents")
        guide, staff_guide = (messages[0]["content"] for messages, _, _ in engine.calls)
        self.assertEqual([model for _, _, model in engine.calls],[CHAT_MODEL,CHAT_MODEL])
        self.assertIn("Справочник текущего интерфейса", guide)
        self.assertIn("Подать заявку", guide)
        self.assertIn("Очередь обращений", staff_guide)
        self.assertNotIn("Личная задача", staff_guide)
        self.assertNotIn("student-a", guide)

    def test_assistant_total_budget_persists_after_midnight(self):
        engine = self.model({"action":"guide","page":"photo"})
        engine.total_limit = 1
        a = self.client("student-a")
        route = "/assistant"
        accept = {"HTTP_ACCEPT":"application/json"}
        message = "Как разобрать фотографию расписания на занятия?"
        fields = {**a.fields(route,route),"message":message}
        self.assertEqual(a.request(route,fields,**accept)[0],200)
        with at(now()+timedelta(days=1)):
            b = self.client("student-b")
            result = b.request(route,{**b.fields(route,route),"message":message},**accept)
        self.assertEqual(result[0],429)
        self.assertIn("Общий лимит",json.loads(result[2])["error"])
        self.assertEqual(len(engine.calls),1)

    def test_onboarding_is_role_specific_persists_and_skips(self):
        a, b, staff = (self.client(name) for name in ("student-a", "student-b", "staff"))
        self.assertIn('data-dean-overlay', a.request("/")[2])
        self.assertNotIn('data-dean-overlay', a.request("/schedule")[2])
        for client, routes in (
            (a, ("/", "/schedule", "/grades", "/requests", "/all-services", "/bookings",
                 "/lecture-library", "/notifications", "/assistant")),
            (staff, ("/staff", "/staff", "/staff/grades", "/staff", "/staff/manage", "/staff/bookings",
                     "/staff/announcements", "/lecture-library", "/assistant")),
        ):
            for number, path in enumerate(routes, 1):
                code, headers, _ = client.request(f"/onboarding?step={number}")
                self.assertEqual(code, 303)
                self.assertEqual(urlsplit(headers["Location"]).path, path)
                code, _, html = client.request(headers["Location"])
                self.assertEqual(code, 200)
                self.assertIn(f"Шаг {number} из 9", html)
                self.assertIn('data-tour-anchor=', html)
        self.assertNotIn('data-dean-overlay', a.request("/profile?tour=3")[2])
        finish = "/onboarding/finish"
        self.assertEqual(staff.request(finish, staff.fields("/assistant?tour=9", finish))[1]["Location"], "/staff")
        self.assertNotIn('data-dean-overlay', self.read_after_restart(staff, "/staff"))
        skip = "/onboarding/skip"
        fields = a.fields("/schedule?tour=2", skip)
        self.assertEqual(a.request(skip, {**fields, "next": "//example.invalid"})[1]["Location"], "/")
        self.assertNotIn('data-dean-overlay', self.read_after_restart(a, "/"))
        self.assertIn("Шаг 1 из 9", b.request("/")[2])
        self.assertEqual(self.db_one("SELECT count(*) FROM onboarding"), 2)

    def test_assistant_history_survives_refresh_and_is_private(self):
        a, b = (self.client(name) for name in ("student-a", "student-b"))
        accept = {"HTTP_ACCEPT": "application/json"}
        start = "/assistant?chat=new"
        payload = {**a.fields(start, "/assistant"), "message": "Создай задачу Проверить историю"}
        status, _, body = a.request("/assistant", payload, **accept)
        self.assertEqual(status, 200)
        first = json.loads(body)
        self.assertEqual(json.loads(a.request("/assistant", payload, **accept)[2])["chat"], first["chat"])
        self.assertEqual(self.db_one("SELECT COUNT(*) FROM assistant_message"), 1)
        path = "/assistant?chat=" + first["chat"]
        self.assertIn("Проверить историю", self.read_after_restart(a, path))
        self.assertEqual(b.request(path)[0], 404)
        self.assertNotIn("Проверить историю", b.request("/assistant")[2])
        self.assertNotIn("Проверить историю", a.request(start)[2].split("data-chat-log")[1])
        confirm = "/assistant/actions/" + first["proposal"] + "/confirm"
        self.assertEqual(a.request(confirm, a.fields(path, confirm))[0], 303)
        self.assertIn("Действие выполнено", a.request(path)[2])

    def test_share_selected_manual_and_scanned_events_copy_and_revoke(self):
        event = {"day":1,"start":"08:50","end":"10:25","subject":"Пара со снимка","room":"А-301",
                 "kind":"семинар","uncertain":False}
        self.model({"events":[event]})
        a,b,guest = self.client("student-a"),self.client("student-b"),self.client()
        first = self.today - timedelta(days=self.today.weekday())
        photo = upload(a, PHOTO, {**a.fields(PHOTO,PHOTO),"week":first.isoformat()}, PIXEL)[1]["Location"]
        self.assertEqual(a.request(photo+"/confirm",a.fields(photo,photo+"/confirm"))[0],303)
        for title in ("Пара вручную","Личное дело"):
            path = "/events/new"
            fields = {**a.fields(path,path),"title":title,"description":"Текст только для себя","location":"Зал",
                      "starts":first.isoformat()+"T12:00","ends":first.isoformat()+"T13:30","all_day":""}
            self.assertEqual(a.request(path,fields)[0],303)
        root = "/calendar/share"
        page = root+"?week="+first.isoformat()
        html = a.request(page)[2]
        keys = re.findall(r'<input type="checkbox" name="event" value="([0-9a-f]+)">\s*<span>Пара ',html)
        self.assertEqual(len(keys),2)
        fields = a.fields(page,root)
        payload = urlencode([*fields.items(),*(("event",key) for key in keys)]).encode()
        result = a.request(root,payload)
        self.assertEqual(result[0],201)
        self.assertEqual(a.request(root,payload)[0],409)
        link = re.search(r'href="(/calendar/shared/[A-Za-z0-9_-]{43})"',result[2])[1]
        self.assertIn(f'href="{link}" class="dean-action-link" target="_blank" rel="noopener"', result[2])
        response = guest.request(link)
        self.assertEqual(response[0],200)
        self.assertEqual(response[1]["Referrer-Policy"],"no-referrer")
        self.assertEqual(response[1]["Cache-Control"],"no-store")
        for title in ("Пара со снимка","Пара вручную","А-301"):
            self.assertIn(title,response[2])
        for private in ("Личное дело","Текст только для себя"):
            self.assertNotIn(private,response[2])
        self.assertEqual(guest.request(link+"/copy",{})[0],403)
        target = link+"/copy"
        before = self.db_one("SELECT count(*) FROM personal_event")
        self.assertEqual(b.request(link)[1]["Referrer-Policy"],"same-origin")
        copy = b.fields(link,target)
        self.assertEqual(b.request(target,copy,HTTP_ORIGIN="null")[0],403)
        status, headers, _ = b.request(target,copy,HTTP_ORIGIN="http://127.0.0.1")
        self.assertEqual(status,303)
        draft = headers["Location"]
        self.assertEqual(self.db_one("SELECT count(*) FROM personal_event"),before)
        self.assertEqual(b.request(draft+"/confirm",b.fields(draft,draft+"/confirm"))[0],303)
        self.assertEqual(self.db_one("SELECT count(*) FROM personal_event"),before+2)
        with at(now()+timedelta(days=8)):
            self.assertEqual(guest.request(link)[0],404)
        sid = self.db_one("SELECT id FROM calendar_share")
        revoke = root + "/" + sid + "/revoke"
        self.assertEqual(b.request(revoke,{"csrf":b.fields("/","/logout")["csrf"]})[0],404)
        self.assertEqual(a.request(revoke,a.fields(page,revoke))[0],303)
        self.assertEqual(guest.request(link)[0],404)
        from tests.test_dean import Forms
        status, headers, html = b.request(link)
        self.assertEqual((status, headers["Referrer-Policy"]), (404, "same-origin"))
        logout_form = Forms(html).forms["/logout"]
        self.assertEqual(b.request("/logout", logout_form, HTTP_ORIGIN="http://127.0.0.1")[0], 303)
        self.assertEqual(b.request("/profile")[0], 303)
