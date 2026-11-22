from contextlib import closing
from datetime import timedelta
from io import BytesIO
import json
from pathlib import Path
import subprocess
import sys
from threading import Event, Thread
from unittest.mock import patch
from uuid import uuid4

from dean import lecture_analysis, lecture_channel, lecture_imports, lecture_youtube
from dean.clock import at, now
from dean.db import connect
from dean.domain import timestamp
from scripts.lecture_worker import claim
from tests.test_dean import Client, DemoFixture


IMPORT = "/lectures/import"
VIDEO = b"\x00\x00\x00\x18ftypmp42" + b"\x00" * (3 * lecture_imports.CHUNK)


def public_video_host(pages):
    """Serve fixture responses at their configured HTTPS request paths."""
    class Response(BytesIO):
        def __init__(self, status, headers, body):
            super().__init__(body)
            self.status = status
            self.headers = headers

        @property
        def length(self):
            return len(self.getbuffer()) - self.tell()

        def getheader(self, name, default=None):
            return self.headers.get(name, default)

    class Host:
        def __init__(self, hostname, timeout):
            self.hostname = hostname

        def request(self, method, path, headers):
            self.path = path

        def getresponse(self):
            return Response(*pages[(self.hostname, self.path)])

        def close(self):
            pass

    return patch.object(lecture_imports, "PublicHTTPSConnection", Host)


def multipart(fields, video):
    boundary = "lecture-test-" + uuid4().hex
    parts = [f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n{value}\r\n'.encode()
             for name,value in fields.items()]
    parts += [f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="Лекция.mp4"\r\n'
              'Content-Type: video/mp4\r\n\r\n'.encode(),video,f'\r\n--{boundary}--\r\n'.encode()]
    return b"".join(parts),f"multipart/form-data; boundary={boundary}"


class LectureImportTest(DemoFixture):
    def setUp(self):
        super().setUp()
        flags=patch.dict("os.environ",{"DEAN_CAMPUS_LAB":"1","DEAN_LECTURE_WORKER":"1",
                                      "DEAN_LECTURE_LIBRARY_DIR":str(self.path.parent)})
        flags.start()
        self.addCleanup(flags.stop)
        self.staff,self.student,self.other=(Client(self.app) for _ in range(3))
        for client,login in zip((self.staff,self.student,self.other),("staff","student-a","student-b")):
            self.assertEqual(client.login(login,self.passwords[login])[0],303)
        with closing(connect(self.path)) as db:
            actor=db.execute("SELECT * FROM account WHERE username='staff'").fetchone()
            self.scope=next(scope for scope in lecture_channel.import_options(db,actor) if scope.startswith("demo-ai|"))

    def form(self,action,title):
        values=self.staff.fields(IMPORT,IMPORT+action)
        return {"csrf":values["csrf"],"request_id":values["request_id"],"scope":self.scope,"title":title}

    def upload(self,client,fields,video=VIDEO,**options):
        data,mime=multipart(fields,video)
        return client.request(IMPORT+"/upload",data,CONTENT_TYPE=mime,**options)

    def run_worker(self, engine=None):
        task=claim(self.path)
        self.assertIsNotNone(task)
        lid,token,cloud,model=task
        from dean.ai import RouterAI
        lecture_channel.analysis_job(self.path,lid,token,engine or RouterAI(),cloud,model)
        self.assertIsNone(claim(self.path))

    def test_upload_materials_and_private_group_access(self):
        fields=self.form("/upload","Базы данных · загрузка")
        self.assertEqual(self.student.request(IMPORT)[0],200)
        self.assertEqual(self.upload(self.student,fields)[0],403)
        self.assertEqual(self.upload(self.staff,fields,HTTP_ORIGIN="https://other.example")[0],403)
        self.assertEqual(self.upload(self.staff,fields|{"csrf":"bad"})[0],403)
        self.assertEqual(self.upload(self.staff,fields,b"invalid media")[0],422)
        created=self.upload(self.staff,fields)
        self.assertEqual(created[0],303)
        url=created[1]["Location"]
        self.assertEqual(self.upload(self.staff,fields)[1]["Location"],url)
        self.assertEqual(self.upload(self.staff,fields,VIDEO+b"x")[0],409)
        self.assertEqual(self.db_one("SELECT count(*) FROM lab_lecture"),1)
        self.assertEqual(self.db_one("SELECT count(*) FROM lecture_analysis_job"),1)
        self.assertIn(url,self.student.request("/lecture-library")[2])
        self.assertNotIn(url,self.other.request("/lecture-library")[2])
        self.assertEqual(self.other.request(url+"/recording",REQUEST_METHOD="HEAD")[0],404)
        self.assertEqual(self.student.request(url+"/homework.pdf")[0],404)
        code,headers,body=self.student.request(url+"/recording",HTTP_RANGE="bytes=4-7")
        self.assertEqual((code,body),(206,"ftyp"))
        self.assertIn(f"/"+str(len(VIDEO)),headers["Content-Range"])
        self.assertEqual(self.other.request(url+"/status")[0],404)
        self.assertTrue(json.loads(self.student.request(url+"/status")[2])["running"])

        text="Ключ определяет запись. Индекс ускоряет поиск. Сортировка меняет порядок строк."
        with patch("dean.lecture_analysis.transcribe",return_value=(text,"Тестовый Whisper")) as transcribe:
            self.run_worker()
            clip,=transcribe.call_args.args[0]
            self.assertIsInstance(clip,Path)
            self.assertEqual(clip.read_bytes(),VIDEO)
        self.assertTrue(json.loads(self.student.request(url+"/status")[2])["ready"])
        self.assertIn("Конспект",self.student.request(url)[2])
        quiz = json.loads(self.db_one("SELECT quiz_json FROM lab_lecture"))
        self.assertEqual(len(quiz), 3)
        self.assertIn("Тест по лекции", self.student.request(url)[2])
        quiz_url = url + "/quiz"
        fields = self.student.fields(url, quiz_url)
        choice = quiz[0]["correct"]
        self.assertEqual(self.student.request(quiz_url, fields | {"question":"0", "answer":"99"})[0], 422)
        self.assertEqual(self.student.request(quiz_url, fields | {"question":"0", "answer":str(choice)})[1]["Location"],
                         url + "#question-0")
        self.assertIn("Верно.", self.student.request(url)[2])
        self.assertIn("Ответы <strong>1 из 3", self.student.request(url)[2])
        self.assertIn("Ответы <strong>0 из 3", self.staff.request(url)[2])
        wrong = str((choice + 1) % len(quiz[0]["options"]))
        self.assertEqual(self.student.request(quiz_url, fields | {"question":"0", "answer":wrong})[0], 303)
        self.assertIn("Пока неверно.", self.student.request(url)[2])
        self.assertEqual(self.db_one("SELECT count(*) FROM lab_lecture_quiz_answer"), 1)
        other = self.other.fields(IMPORT, IMPORT + "/upload")
        self.assertEqual(self.other.request(quiz_url, other | {"question":"0", "answer":str(choice)})[0], 404)
        self.assertEqual(self.db_one("SELECT count(*) FROM lecture_pdf"),1)
        for kind in ("notes","homework"):
            pdf=url+f"/{kind}.pdf"
            self.assertEqual(self.other.request(pdf)[0],404)
            code,headers,payload=self.student.request(pdf+"?download=1")
            self.assertEqual((code,headers["Content-Type"]),(200,"application/pdf"))
            self.assertTrue(headers["Content-Disposition"].startswith("attachment;"))
            self.assertTrue(payload.startswith(b"%PDF-"))
            self.assertEqual(self.student.request(pdf)[1]["Content-Disposition"].split(";")[0],"inline")

    def test_analyzed_lecture_from_before_quiz_upgrade_can_be_answered(self):
        url = self.upload(self.staff, self.form("/upload", "Лекция до обновления"))[1]["Location"]
        transcript = "Таблица хранит записи. Индекс ускоряет поиск."
        with patch("dean.lecture_analysis.transcribe", return_value=(transcript, "Тестовый Whisper")):
            self.run_worker()
        with closing(connect(self.path)) as db:
            db.execute("UPDATE lab_lecture SET quiz_json=NULL")
        self.assertIn("Тест по лекции", self.student.request(url)[2])
        self.assertIsNone(self.db_one("SELECT quiz_json FROM lab_lecture"))
        quiz = lecture_analysis.local_quiz(transcript)
        quiz_url = url + "/quiz"
        fields = self.student.fields(url, quiz_url)
        self.assertEqual(self.student.request(quiz_url, fields | {"question":"0", "answer":str(quiz[0]["correct"])})[0], 303)
        self.assertEqual(json.loads(self.db_one("SELECT quiz_json FROM lab_lecture")), quiz)
        self.assertIn("Верно.", self.student.request(url)[2])

    @patch.dict("os.environ", {"DEAN_LECTURE_PUBLIC_LINKS": "0"})
    def test_external_links_stay_external_when_public_links_are_disabled(self):
        fields=self.form("/link","Запись по ссылке")
        self.assertEqual(self.staff.request(IMPORT+"/link",fields|{"url":"http://video.example.com/lecture.mp4"})[0],422)
        result=self.staff.request(IMPORT+"/link",fields|{"url":"https://video.example.com/lecture.mp4"})
        self.assertEqual(result[0],303)
        url=result[1]["Location"]
        self.assertIn("Открыть видео по ссылке",self.student.request(url)[2])
        self.assertEqual(self.db_one("SELECT count(*) FROM lecture_analysis_job"),0)
        values=self.staff.fields(url,IMPORT+"/upload")
        fields={name:values[name] for name in ("csrf","request_id","lecture")}
        self.assertEqual(self.upload(self.staff,fields)[1]["Location"],url)
        self.assertEqual(self.upload(self.staff,fields)[1]["Location"],url)
        self.assertEqual(self.db_one("SELECT count(*) FROM lab_lecture"),1)
        self.assertEqual(self.db_one("SELECT count(*) FROM lecture_analysis_job"),1)

    def test_trusted_link_download_and_built_in_materials(self):
        with patch.dict("os.environ",{"DEAN_LECTURE_IMPORT_HOSTS":"media.example.com"}):
            fields=self.form("/link","Видео из медиатеки")
            source="https://media.example.com/lecture.mp4"
            code,headers,_=self.staff.request(IMPORT+"/link",fields|{"url":source})
            self.assertEqual(code,303)
            with patch("dean.lecture_imports.download",side_effect=lambda url,path,**_: (path.write_bytes(VIDEO),"video/mp4")[1]) as download, \
                    patch("dean.lecture_analysis.transcribe",return_value=("Работа индексов.","Тестовый Whisper")):
                self.run_worker()
                self.assertEqual(download.call_args.args[0],source)
        self.assertEqual(self.student.request(headers["Location"]+"/notes.pdf")[0],200)
        for kind in ("notes","homework"):
            code,headers,body=self.student.request(f"/lecture-library/semantic-web/intro/{kind}.pdf")
            self.assertEqual((code,headers["Content-Type"]),(200,"application/pdf"))
            self.assertTrue(body.startswith(b"%PDF-"))

    def test_student_link_is_private_and_homework_creates_a_personal_task(self):
        action = IMPORT + "/link"
        fields = self.student.fields(IMPORT, action)
        source = "https://media.example.com/lecture.mp4"
        fields |= {"url": source, "title": "Моя запись по базам данных"}
        with closing(connect(self.path)) as db:
            db.execute("UPDATE account SET group_id='demo-ai' WHERE username='student-b'")
        with patch.dict("os.environ", {"DEAN_LECTURE_IMPORT_HOSTS": "media.example.com"}):
            self.assertEqual(self.student.request(action, fields | {"scope": "demo-energy|fake|"})[0], 403)
            code, headers, _ = self.student.request(action, fields)
            self.assertEqual(code, 303)
            detail = headers["Location"]
            self.assertEqual(self.student.request(action, fields)[1]["Location"], detail)
            conflict = self.student.request(action, fields | {"request_id": uuid4().hex, "title": "Вторая лекция"})
            self.assertEqual(conflict[0], 409)
            self.assertIn('href="' + detail + '"', conflict[2])
            self.assertIn('value="Вторая лекция"', conflict[2])
            self.assertIn("Открыть статус обработки", self.student.request(IMPORT)[2])
            self.assertIn("Личные лекции", self.student.request("/lecture-library")[2])
            for outsider in (self.staff, self.other):
                self.assertNotIn(detail, outsider.request("/lecture-library")[2])
                self.assertEqual(outsider.request(detail)[0], 404)
            with patch("dean.lecture_imports.download", side_effect=lambda url, path, **_: (path.write_bytes(VIDEO), "video/mp4")[1]), \
                    patch("dean.lecture_analysis.transcribe", return_value=("Индекс ускоряет поиск.", "Тестовый Whisper")):
                self.run_worker()
        path = detail + "/task"
        task_form = self.student.fields(detail, path)
        code, headers, _ = self.student.request(path, task_form)
        self.assertEqual(code, 303)
        task_url = headers["Location"]
        task_page = self.student.request(task_url)[2]
        self.assertIn("Индекс ускоряет поиск.", task_page)
        self.assertIn("К лекции и домашнему заданию", task_page)
        lecture_page = self.student.request(detail)[2]
        self.assertIn(f'href="{task_url}"', lecture_page)
        self.assertNotIn("Добавить в мои задачи", lecture_page)
        self.assertEqual(self.student.request(path, task_form)[1]["Location"], task_url)
        self.assertEqual(self.student.request(path, task_form | {"request_id": uuid4().hex})[1]["Location"], task_url)
        self.assertEqual(self.db_one("SELECT count(*) FROM task WHERE title='Домашняя работа · Моя запись по базам данных'"), 1)
        self.assertEqual(self.other.request(task_url)[0], 404)

    def test_legacy_homework_uses_the_full_sentences_from_the_transcript(self):
        short_sentence = "Разработчик может сравнить два запроса."
        long_sentence = "Для начала сравнения проверяем " + "деталь " * 30 + "в конце примера."
        homework = lecture_channel.lecture_homework({
            "transcript": long_sentence + " " + short_sentence,
            "homework": f"1. Объясни своими словами фрагмент «{long_sentence[:180]}».\n"
                        f"2. Приведи пример, который помогает понять мысль «{short_sentence}».",
            "source": "Тестовый Whisper · локальная выборка фрагментов и задания по ним",
        })
        self.assertNotIn(long_sentence[:180], homework)
        self.assertIn(f"«{short_sentence}»", homework)

    def test_owner_can_stop_processing_and_submit_another_lecture(self):
        action = IMPORT + "/link"
        source = "https://media.w3.org/sample.mp4"
        first_fields = self.student.fields(IMPORT, action) | {"title": "Первая лекция", "url": source}
        moment = now()
        with at(moment):
            detail = self.student.request(action, first_fields)[1]["Location"]
        with at(moment + timedelta(minutes=3)):
            status = json.loads(self.student.request(detail + "/status")[2])
            self.assertIn("задерживается", status["detail"])
            self.assertEqual(status["status"], "В очереди на обработку")
            job = claim(self.path)
        self.assertEqual(job[0], detail.rsplit("/", 1)[1])
        stop = detail + "/stop"
        other_fields = self.other.fields(IMPORT, action)
        self.assertEqual(self.other.request(stop, {"csrf": other_fields["csrf"]})[0], 404)
        self.assertEqual(self.student.request(stop, self.student.fields(detail, stop))[0], 303)
        status = json.loads(self.student.request(detail + "/status")[2])
        self.assertFalse(status["running"])
        self.assertEqual(status["status"], "Обработка остановлена")
        self.assertIn("Повторить обработку", self.student.request(detail)[2])
        second_fields = self.student.fields(IMPORT, action) | {"title": "Вторая лекция", "url": source}
        self.assertEqual(self.student.request(action, second_fields)[0], 303)
        self.assertEqual(self.db_one("SELECT count(*) FROM lecture_analysis_job"), 1)

    def test_stopping_a_lecture_releases_the_worker_during_transcription(self):
        action = IMPORT + "/link"
        fields = self.student.fields(IMPORT, action) | {"title": "Долгая лекция", "url": "https://media.w3.org/sample.mp4"}
        detail = self.student.request(action, fields)[1]["Location"]
        task = claim(self.path)
        self.assertIsNotNone(task)
        lid, token, cloud, model = task
        entered = Event()

        def slow_transcription(*_args, require_active, **_options):
            started = False

            def check_active():
                nonlocal started
                require_active()
                if started:
                    entered.set()
                started = True

            lecture_analysis.invoke([sys.executable, "-c", "import time; time.sleep(15)"],
                                    20, "Тестовое распознавание", require_active=check_active)

        from dean.ai import RouterAI
        thread = Thread(target=lecture_channel.analysis_job,
                        args=(self.path, lid, token, RouterAI(), cloud, model), daemon=True)
        with patch("dean.lecture_imports.download", side_effect=lambda url, path, **_: (path.write_bytes(VIDEO), "video/mp4")[1]), \
             patch("dean.lecture_analysis.transcribe", side_effect=slow_transcription):
            thread.start()
            try:
                self.assertTrue(entered.wait(4))
                stop = detail + "/stop"
                self.assertEqual(self.student.request(stop, self.student.fields(detail, stop))[0], 303)
            finally:
                thread.join(4)
        self.assertFalse(thread.is_alive(), "stopped transcription still owns the worker")
        self.assertEqual(json.loads(self.student.request(detail + "/status")[2])["status"], "Обработка остановлена")
        self.assertEqual(self.student.request(detail + "/recording", REQUEST_METHOD="HEAD")[0], 200)
        self.assertIsNone(claim(self.path))

    def test_worker_recovers_once_then_releases_abandoned_job(self):
        action = IMPORT + "/link"
        values = self.student.fields(IMPORT, action) | {"title": "Лекция", "url": "https://media.w3.org/sample.mp4"}
        moment = now()
        with at(moment):
            detail = self.student.request(action, values)[1]["Location"]
            first = claim(self.path)
            self.assertIsNone(claim(self.path))
        with at(moment + timedelta(minutes=3)):
            status = json.loads(self.student.request(detail + "/status")[2])
            self.assertEqual(status["status"], "Ожидаем повторного запуска")
            second = claim(self.path)
            self.assertNotEqual(first[1], second[1])
            self.assertEqual(self.db_one("SELECT attempts FROM lecture_analysis_job"), 2)
        with at(moment + timedelta(minutes=6)):
            self.assertIsNone(claim(self.path))
            status = json.loads(self.student.request(detail + "/status")[2])
            self.assertFalse(status["running"])
            self.assertIn("прервалась", status["error"])
            retry = detail + "/analyse"
            self.assertEqual(self.student.request(retry, self.student.fields(detail, retry))[0], 303)
            self.assertEqual(self.db_one("SELECT attempts FROM lecture_analysis_job"), 0)

    def test_commons_file_with_connected_media_host_is_resolved(self):
        source = "https://commons.wikimedia.org/wiki/File%3ALecture%20example.webm"
        with patch.dict("os.environ", {"DEAN_LECTURE_IMPORT_HOSTS": "upload.wikimedia.org"}):
            self.assertEqual(
                lecture_imports.recording_source(source),
                "https://commons.wikimedia.org/wiki/Special:FilePath/Lecture%20example.webm",
            )
        with patch.dict("os.environ", {"DEAN_LECTURE_IMPORT_HOSTS": ""}):
            self.assertIsNone(lecture_imports.recording_source(source))

    def test_commons_transfer_reports_stages_and_saved_mebibytes(self):
        source = "https://commons.wikimedia.org/wiki/File:Lecture.webm"
        video = b"\x1a\x45\xdf\xa3" + b"\0" * (1024 * 1024)
        callback = []
        pages = {
            ("commons.wikimedia.org", "/wiki/Special:FilePath/Lecture.webm"):
                (302, {"Location": "https://upload.wikimedia.org/wikipedia/commons/Lecture.webm"}, b""),
            ("upload.wikimedia.org", "/wikipedia/commons/Lecture.webm"): (200, {"Content-Type": "video/webm"}, video),
        }
        target = self.path.parent / "sample.webm"
        with public_video_host(pages):
            self.assertEqual(lecture_imports.download(source, target, progress=callback.append), "video/webm")
        self.assertEqual(target.read_bytes(), video)
        self.assertEqual(callback, ["Проверяем источник видео", "Загружаем видео", "Загружаем видео · 1 МиБ"])

    def test_public_page_finds_video_and_saves_materials_in_the_worker(self):
        source = "https://learn.example.com/classes/recording"
        pages = {
            ("learn.example.com", "/classes/recording"): (302, {"Location": "/lesson/recording/"}, b""),
            ("learn.example.com", "/lesson/recording/"):
                (200, {"Content-Type": "text/html; charset=utf-8"},
                 b'<video><source src="playlist.m3u8" type="application/vnd.apple.mpegurl">'
                 b'<source src="https://cdn.example.com/lecture.mp4?quality=medium" type="video/mp4"></video>'),
            ("cdn.example.com", "/lecture.mp4?quality=medium"): (200, {"Content-Type": "video/mp4"}, VIDEO),
        }
        action = IMPORT + "/link"
        fields = self.student.fields(IMPORT, action)
        with patch.dict("os.environ", {"DEAN_LECTURE_IMPORT_HOSTS": "learn.example.com,cdn.example.com"}), \
                public_video_host(pages), \
                patch("dean.lecture_analysis.transcribe", return_value=("Индексы ускоряют поиск.", "Тестовый Whisper")):
            code, headers, _ = self.student.request(action, fields | {"title": "Открытая лекция", "url": source})
            self.assertEqual(code, 303)
            route = headers["Location"]
            self.assertEqual(self.db_one("SELECT count(*) FROM lecture_analysis_job"), 1)
            self.run_worker()
        card = self.student.request(route)[2]
        self.assertIn("Индексы ускоряют поиск.", card)
        self.assertIn(source, card)
        for kind in ("notes", "homework"):
            self.assertEqual(self.student.request(route + f"/{kind}.pdf")[0], 200)
        self.assertEqual(self.other.request(route)[0], 404)

    def test_saved_page_can_be_processed_after_connecting_its_host(self):
        source = "https://www.w3.org/WAI/videos/standards-and-benefits/"
        pages = {
            ("www.w3.org", "/WAI/videos/standards-and-benefits/"):
                (200, {"Content-Type": "text/html"},
                 b'<script type="application/ld+json">{"@type":"VideoObject",'
                 b'"contentUrl":"https://media.w3.org/sample.mp4"}</script>'),
            ("media.w3.org", "/sample.mp4"): (200, {"Content-Type": "video/mp4"}, VIDEO),
        }
        action = IMPORT + "/link"
        fields = self.form("/link", "Сохранённая лекция")
        with patch.dict("os.environ", {"DEAN_LECTURE_IMPORT_HOSTS": ""}):
            code, headers, _ = self.staff.request(action, fields | {"url": source})
            self.assertEqual(code, 303)
            route = headers["Location"]
            self.assertIn("источник пока не подключён", self.staff.request(route)[2])
            self.assertEqual(self.db_one("SELECT count(*) FROM lecture_analysis_job"), 0)
        with patch.dict("os.environ", {"DEAN_LECTURE_IMPORT_HOSTS": "media.w3.org"}), public_video_host(pages), \
                patch("dean.lecture_analysis.transcribe", return_value=("Доступность в интернете.", "Тестовый Whisper")):
            retry = route + "/analyse"
            self.assertIn("Найти видео и обработать", self.staff.request(route)[2])
            self.assertEqual(self.staff.request(retry, self.staff.fields(route, retry))[0], 303)
            self.run_worker()
        self.assertEqual(self.student.request(route + "/notes.pdf")[0], 200)
        self.assertEqual(self.db_one("SELECT count(*) FROM lab_lecture"), 1)

    def test_public_lecture_page_queues_original_video_from_its_markup(self):
        source = "https://open.campus.example/lecture.html"
        pages = {
            ("open.campus.example", "/lecture.html"): (
                200, {"Content-Type": "text/html"},
                b'<video><source src="https://video.campus.example/lesson.mp4" type="video/mp4"></video>'),
            ("video.campus.example", "/lesson.mp4"): (200, {"Content-Type": "video/mp4"}, VIDEO),
        }
        with patch.dict("os.environ", {"DEAN_LECTURE_PUBLIC_LINKS": "1"}), public_video_host(pages), \
                patch("dean.lecture_analysis.transcribe", return_value=("Конспект лекции.", "Тестовый Whisper")):
            response = self.staff.request(IMPORT + "/link", self.form("/link", "Открытая лекция") | {"url": source})
            self.assertEqual(response[0], 303)
            self.assertEqual(self.db_one("SELECT count(*) FROM lecture_analysis_job"), 1)
            self.run_worker()
        self.assertEqual(self.student.request(response[1]["Location"] + "/notes.pdf")[0], 200)

    def test_youtube_link_from_report_imports_audio_and_publishes_materials(self):
        source = "https://www.youtube.com/watch?v=634rrWx4rXg"
        spawn = subprocess.Popen
        executed = []

        def fake_youtube(args, **options):
            executed.append(args)
            self.assertNotIn("DEEPSEEK_API_KEY", options["env"])
            return spawn([sys.executable, "-c", "import sys; sys.stdout.buffer.write(bytes.fromhex(sys.argv[1]))",
                          VIDEO[:28].hex()], **options)

        flags = {"DEAN_LECTURE_PUBLIC_LINKS": "1"}
        with patch.dict("os.environ", flags), patch.object(lecture_youtube.shutil, "which", return_value="/test/tool"), \
                patch.object(lecture_youtube.subprocess, "Popen", side_effect=fake_youtube), \
                patch("dean.lecture_analysis.transcribe", return_value=("Текст видео по ссылке.", "Тестовый Whisper")):
            action = IMPORT + "/link"
            code, headers, _ = self.staff.request(action, self.form("/link", "Видео") | {"url": source + "&list=PLdemo"})
            self.assertEqual(code, 303)
            self.run_worker()
        route = headers["Location"]
        self.assertEqual(len(executed), 1)
        self.assertEqual(executed[0][-2:], ["--", source])
        self.assertIn("--no-playlist", executed[0])
        self.assertIn('<audio controls', self.student.request(route)[2])
        self.assertEqual(self.student.request(route + "/recording", REQUEST_METHOD="HEAD")[1]["Content-Type"], "audio/mp4")
        self.assertEqual(self.student.request(route + "/notes.pdf")[0], 200)

    def test_w3c_perspectives_page_imports_its_embedded_youtube_video(self):
        path = "/WAI/perspective-videos/captions/"
        source = "https://www.w3.org" + path
        # Match the public WAI site's generated video element.
        markup = b'<video preload="metadata" data-youtube-id="iWO5N3n1DXU"><track kind="captions"></video>'
        pages = {("www.w3.org", path): (200, {"Content-Type": "text/html"}, markup)}

        def audio(_url, destination, _limit, **_options):
            destination.write_bytes(VIDEO)
            return "audio/mp4"

        with patch.dict("os.environ", {"DEAN_LECTURE_IMPORT_HOSTS": "media.w3.org,www.youtube.com",
                                       "DEAN_LECTURE_PUBLIC_LINKS": "0"}), \
             public_video_host(pages), patch.object(lecture_youtube, "download", side_effect=audio) as downloader, \
             patch("dean.lecture_analysis.transcribe", return_value=("Текст с субтитрами.", "Тестовый Whisper")):
            action = IMPORT + "/link"
            code, headers, _ = self.staff.request(action, self.form("/link", "Видео WAI") | {"url": source})
            self.assertEqual(code, 303)
            self.run_worker()
        self.assertEqual(downloader.call_args.args[0], "https://www.youtube.com/watch?v=iWO5N3n1DXU")
        detail = headers["Location"]
        self.assertEqual(self.student.request(detail + "/recording", REQUEST_METHOD="HEAD")[1]["Content-Type"], "audio/mp4")
        self.assertEqual(self.student.request(detail + "/notes.pdf")[0], 200)

    def test_youtube_playlist_is_rejected_without_selecting_a_recording(self):
        source = "https://www.youtube.com/playlist?list=PLdemo"
        with patch.dict("os.environ", {"DEAN_LECTURE_PUBLIC_LINKS": "1"}):
            self.assertIsNone(lecture_imports.recording_source(source))
            self.assertEqual(lecture_imports.recording_source("https://youtu.be/634rrWx4rXg?si=demo"),
                             "https://www.youtube.com/watch?v=634rrWx4rXg")
            code, _, page = self.staff.request(IMPORT + "/link", self.form("/link", "Список") | {"url": source})
            self.assertEqual(code, 422)
            self.assertIn("Укажи ссылку на одно видео YouTube", page)

    def test_external_model_requires_opt_in_and_is_budgeted(self):
        class Model:
            enabled = True
            provider_name = "Тестовая модель"
            daily_limit = 40
            total_limit = 300

            def __init__(self):
                self.lengths = []

            def complete(self, messages, max_tokens, *, model):
                self.lengths.append(len(messages[1]["content"]))
                return {"notes": "## Индексы\nИндекс ускоряет поиск.",
                        "homework": "1. Сравни два индекса.",
                        "quiz": [{"question":"Зачем нужен индекс?", "options":["Для поиска", "Для удаления"],
                                  "correct":0, "explanation":"Индекс ускоряет поиск записей."}]}

        engine = Model()
        transcript = "Индексы ускоряют поиск записей. " * 1550
        with patch("dean.lecture_analysis.transcribe", return_value=(transcript, "Тестовый Whisper")):
            self.assertEqual(self.upload(self.staff, self.form("/upload", "Локальная обработка"))[0],303)
            self.run_worker(engine)
            self.assertEqual(engine.lengths, [])
            fields = self.form("/upload", "Обработка с моделью") | {"model": "1"}
            submission = self.upload(self.staff, fields)
            self.assertEqual(submission[0],303)
            self.run_worker(engine)
        self.assertEqual(len(engine.lengths), 1)
        self.assertLess(engine.lengths[0], 36000)
        self.assertEqual(self.db_one("SELECT count(*) FROM ai_request WHERE kind='lecture'"), 1)
        self.assertIn("Зачем нужен индекс?", self.staff.request(submission[1]["Location"])[2])
