import json
from contextlib import closing
from unittest.mock import patch

from dean import lecture_channel
from dean.ai import ProviderFailure, RouterAI
from dean.db import connect
from dean.web import create_app
from tests.test_dean import Client, DemoFixture
from tests.test_lecture_import import IMPORT, VIDEO, public_video_host
from scripts.lecture_worker import claim


WATCH = "/lecture-library/semantic-web/intro"


class Model:
    enabled = True
    provider_name = "RouterAI"
    daily_limit = 30
    total_limit = 50

    def __init__(self):
        self.calls = 0

    def complete(self, messages, max_tokens, *, model):
        self.calls += 1
        if self.calls == 1:
            raise ProviderFailure(503, "private-provider-diagnostic", "provider-balance")
        return {
            "notes": ["00:32 Структура данных помогает описывать страницу."] * 4,
            "practice": ["Проверь разметку страницы.", "Сопоставь поля.", "Объясни результат."],
        }


class LectureRecoveryTest(DemoFixture):
    def test_model_failure_keeps_lesson_and_retry_generates_private_materials(self):
        engine = Model()
        self.app = create_app(self.path, ai_engine=engine)
        student, other = self.client("student-a"), self.client("student-b")
        action = WATCH + "/materials"
        payload = student.fields(WATCH, action)
        status, headers, _ = student.request(action, payload)
        self.assertEqual(status, 303)
        failed = headers["Location"]
        self.assertEqual(failed, WATCH + "?materials=provider-balance#notes")
        self.assertEqual(student.request(action, payload)[1]["Location"], failed)
        self.assertEqual(engine.calls, 1)
        status, _, page = student.request(failed)
        self.assertEqual(status, 200)
        self.assertIn("Сервис ИИ сообщил о нехватке средств", page)
        self.assertIn("Повторить подготовку", page)
        self.assertIn(WATCH + "/notes.pdf", page)
        self.assertNotIn("private-provider-diagnostic", page)
        status, headers, _ = student.request(action, student.fields(failed, action))
        self.assertEqual((status, headers["Location"]), (303, WATCH + "#notes"))
        self.assertIn("Проверь разметку страницы.", student.request(headers["Location"])[2])
        self.assertNotIn("Проверь разметку страницы.", other.request(WATCH)[2])

    @patch.dict("os.environ", {
        "DEAN_CAMPUS_LAB": "1", "DEAN_LECTURE_WORKER": "1", "DEAN_WHISPER_MODEL": "",
        "DEAN_LECTURE_IMPORT_HOSTS": "upload.wikimedia.org",
    })
    def test_wikimedia_source_failure_and_link_replacement_reach_asr_diagnostics(self):
        student, other = Client(self.app), Client(self.app)
        for client, login in ((student, "student-a"), (other, "student-b")):
            self.assertEqual(client.login(login, self.passwords[login])[0], 303)
        action = IMPORT + "/link"
        url = "https://commons.wikimedia.org/wiki/File:Лекция.webm#file"
        pages = {
            ("commons.wikimedia.org", "/wiki/Special:FilePath/%D0%9B%D0%B5%D0%BA%D1%86%D0%B8%D1%8F.webm"):
                (302, {"Location": "https://upload.wikimedia.org/wikipedia/commons/Lecture.webm"}, b""),
            ("upload.wikimedia.org", "/wikipedia/commons/Lecture.webm"):
                (403, {"Content-Type": "text/html"}, b"private-source-diagnostic"),
            ("upload.wikimedia.org", "/wikipedia/commons/new.mp4"):
                (200, {"Content-Type": "video/mp4"}, VIDEO),
        }
        payload = student.fields(IMPORT, action) | {"title": "Моя лекция", "url": url}
        with public_video_host(pages):
            status, headers, _ = student.request(action, payload)
            self.assertEqual(status, 303)
            detail = headers["Location"]
            lid, token, cloud, use_model = claim(self.path)
            lecture_channel.analysis_job(self.path, lid, token, RouterAI(), cloud, use_model)
            status = json.loads(student.request(detail + "/status")[2])
            self.assertFalse(status["running"])
            self.assertIn("Wikimedia отклонил запрос", status["error"])
            card = student.request(detail)[2]
            self.assertIn("Заменить ссылку", card)
            self.assertIn("Загрузить исходный файл", card)
            self.assertNotIn("private-source-diagnostic", card)
            updated = student.fields(detail, action) | {
                "url": "https://upload.wikimedia.org/wikipedia/commons/new.mp4",
            }
            self.assertEqual(student.request(action, updated)[1]["Location"], detail)
            self.assertEqual(self.db_one("SELECT count(*) FROM lab_lecture"), 1)
            lid, token, cloud, use_model = claim(self.path)
            with patch("dean.lecture_analysis.shutil.which", return_value="/test/ffmpeg"):
                lecture_channel.analysis_job(self.path, lid, token, RouterAI(), cloud, use_model)
        status = json.loads(student.request(detail + "/status")[2])
        self.assertFalse(status["running"])
        self.assertIn("недоступно распознавание речи (Whisper)", status["error"])
        self.assertEqual(student.request(detail + "/recording", REQUEST_METHOD="HEAD")[0], 200)
        self.assertEqual(other.request(detail + "/status")[0], 404)
        with closing(connect(self.path)) as db:
            db.execute("UPDATE lab_lecture SET analysis_error=? WHERE id=?", (lecture_channel.LEGACY_INTERRUPTED, lid))
        card = student.request(detail)[2]
        self.assertIn("Сервер не сохранил причину", card)
        self.assertIn("Повторить обработку", card)
