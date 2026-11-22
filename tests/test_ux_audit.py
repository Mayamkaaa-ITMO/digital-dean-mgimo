import json
import re
from contextlib import closing
from datetime import datetime
from hashlib import sha256
from html import escape
from urllib.parse import parse_qs, urlencode, urlsplit
from unittest.mock import patch
from uuid import uuid4

from dean import calendar_import, clock, public_schedule, public_sync, services
from dean.db import connect
from dean.fixtures import demo_id
from dean.seed import UNIVERSITY, seed_id
from dean.static_assets import asset_url
from tests.test_dean import DemoFixture, Forms


SERVICE = "/services/enrolment-certificate"
SOURCE, GROUP = "tusur-timetable", "fvs/516-1"
WEEKS = ("2026-11-16", "2026-11-23")


class UXAuditTest(DemoFixture):
    def test_csv_wrong_header_returns_form_with_all_text_and_corrects_to_preview(self):
        student = self.client("student-a")
        index, post = "/calendar/import", "/calendar/import/csv"
        original = "другой,заголовок\n" + calendar_import.CSV_EXAMPLE.split("\n", 1)[1]
        count = self.db_one("SELECT COUNT(*) FROM calendar_import")
        status, headers, page = student.request(post, {**student.fields(index, post), "data": original})
        self.assertEqual((status, headers["Cache-Control"]), (422, "no-store"))
        self.assertIn("Строка 1. Замени заголовок CSV на " + calendar_import.CSV_HEADER, page)
        self.assertIn('role="alert" tabindex="-1" data-restore-focus', page)
        self.assertIn('aria-describedby="calendar-csv-error">\n' + escape(original), page)
        self.assertEqual(self.db_one("SELECT COUNT(*) FROM calendar_import"), count)
        repaired = student.request(post, {**Forms(page).forms[post], "data": calendar_import.CSV_EXAMPLE})
        self.assertEqual(repaired[0], 303)
        detail = student.request(repaired[1]["Location"])[2]
        self.assertIn("Предпросмотр импорта", detail)
        self.assertIn("Строка 2", detail)
        self.assertIn("Добавится событие", detail)
        self.assertEqual(self.db_one("SELECT COUNT(*) FROM calendar_import"), count + 1)

    def test_csv_template_and_physical_line_errors(self):
        student = self.client("student-a")
        index, post = "/calendar/import", "/calendar/import/csv"
        status, headers, sample = student.request(index + "?template=csv")
        self.assertEqual(status, 200)
        self.assertEqual(headers["Content-Type"], "text/csv; charset=utf-8")
        self.assertEqual(headers["Content-Disposition"], 'attachment; filename="dean-calendar-template.csv"')
        self.assertEqual(sample, calendar_import.CSV_EXAMPLE)
        self.assertEqual(student.request(post, {**student.fields(index, post), "data": sample})[0], 303)
        for invalid in ('три,не,семь', '"открытая кавычка', '</textarea><b>важный текст</b>'):
            source = sample + "\n" + invalid
            status, _, page = student.request(post, {**student.fields(index, post), "data": source})
            self.assertEqual(status, 422)
            self.assertIn("Строка 4.", page)
            self.assertIn(escape(source), page)
        # Maximum-length text must reach the CSV validator even after URL encoding.
        source = "漢" * calendar_import.CSV_TEXT_LIMIT
        status, _, page = student.request(post, {**student.fields(index, post), "data": source})
        self.assertEqual(status, 422)
        self.assertIn(source, page)

    def test_retake_views_follow_admission_places_and_completed_result(self):
        student, other, staff = (self.client(name) for name in ("student-a", "student-b", "staff"))
        debt = demo_id(UNIVERSITY, "retake", "previous-practicum")
        path, manage = "/retakes/" + debt, "/staff/retakes/" + debt
        slot, later = ("/slots/" + demo_id(UNIVERSITY, "retake", f"slot-{offset}") for offset in (2, 5))
        booking = slot + "/book"

        pending = student.request(slot)[2]
        self.assertIn("Ожидает допуска", pending)
        self.assertIn("Проверка отчёта и допуск учебной части.", pending)
        self.assertIn("придёт в уведомлениях", student.request("/retakes")[2])
        self.assertIn("Ожидает допуска", student.request(path)[2])
        self.assertNotIn("свободное место", pending)
        self.assertNotIn("Запись закрыта", pending)
        self.assertNotIn(booking, Forms(pending).forms)
        self.assertEqual(other.request(slot)[0], 404)

        admit = manage + "/admit"
        self.assertEqual(staff.request(admit, {**staff.fields(manage, admit), "reason": "Отчёт проверен."})[0], 303)
        self.assertIn("Можно записаться", student.request(path)[2])
        available = student.request(slot)[2]
        self.assertIn("1 свободное место", available)
        self.assertIn(booking, Forms(available).forms)

        rival_debt = uuid4().hex
        with closing(connect(self.path)) as db:
            db.execute("UPDATE account SET group_id='demo-ai' WHERE username='student-b'")
            db.execute("""
                INSERT INTO learning_result (id,university_id,student_id,period_id,subject_id,kind,outcome)
                VALUES (?,?,?,'demo-previous','demo-practicum','credit','failed')
            """, (rival_debt, UNIVERSITY, seed_id("student-b")))
            db.execute("INSERT INTO retake_debt (id,reason,conditions,admitted) VALUES (?,?,?,1)",
                       (rival_debt, "Тестовая задолженность", "Отчёт проверен"))
        rival_booking = other.request(booking, other.fields(slot, booking))[1]["Location"]
        self.assertIn("Свободных мест нет", student.request(path)[2])
        full = student.request(slot)[2]
        self.assertIn("Свободных мест нет. Выбери другое окно.", full)
        self.assertNotIn(booking, Forms(full).forms)
        cancel = rival_booking + "/cancel"
        self.assertEqual(other.request(cancel, other.fields(rival_booking, cancel))[0], 303)

        booked = student.request(booking, student.fields(slot, booking))[1]["Location"]
        self.assertIn("Твоя запись", student.request(slot)[2])
        self.assertIn("У тебя уже есть запись на пересдачу", student.request(later)[2])
        rid = booked.rsplit("/", 1)[1]
        end = self.db_one("SELECT ends_at FROM reservation WHERE id=?", (rid,))
        with clock.at(datetime.fromisoformat(end)):
            result = manage + "/result"
            self.assertEqual(staff.request(result, {**staff.fields(manage, result), "result": "passed",
                                                   "reason": "Пересдача завершена."})[0], 303)
            self.assertIn("Задолженность закрыта", student.request(path)[2])
            self.assertIn("Пересдача сдана", student.request(path)[2])
            closed = student.request(later)[2]
            self.assertIn("Посмотри результат в истории записей и результатов.", closed)
            self.assertNotIn(later + "/book", Forms(closed).forms)

    def test_uncertain_creation_receipt_only_resolves_for_owner_service_and_action(self):
        student, other = (self.client(name) for name in ("student-a", "student-b"))
        accept = {"HTTP_ACCEPT": "application/json"}
        fields = {**student.fields(SERVICE, SERVICE), "purpose": "Для подтверждения обучения"}
        path = "/requests?" + urlencode({"receipt": fields["request_id"], "service": SERVICE.rsplit("/", 1)[1]})
        self.assertEqual(student.request(path, **accept)[0], 404)
        submitted = student.request(SERVICE, fields, **accept)
        self.assertEqual(submitted[0], 200)
        count = self.db_one("SELECT COUNT(*) FROM application")
        confirmed = student.request(path, **accept)
        self.assertEqual((confirmed[0], confirmed[1]["Cache-Control"], confirmed[2]), (200, "no-store", submitted[2]))
        self.assertEqual(other.request(path, **accept)[0], 404)
        self.assertEqual(student.request(path + "&draft=1", **accept)[0], 404)
        self.assertEqual(student.request(path.replace("enrolment-certificate", "transcript"), **accept)[0], 404)
        self.assertEqual(self.db_one("SELECT COUNT(*) FROM application"), count)

    def test_versioned_assets_cache_without_caching_private_pages(self):
        student = self.client("student-a")
        for path in ("/login", "/", "/schedule", "/all-services", "/requests"):
            client = self.client() if path == "/login" else student
            status, headers, html = client.request(path)
            self.assertEqual(status, 200)
            self.assertEqual(headers["Cache-Control"], "no-store")
            self.assertIn(asset_url("nunito-cyrillic.woff2"), html)
            self.assertIn(asset_url("main.css"), html)
            self.assertIn(asset_url("railway.js"), html)

        cls = self.client()
        stylesheet = asset_url("main.css")
        status, headers, css = cls.request(stylesheet)
        self.assertEqual(status, 200)
        self.assertEqual(headers["Cache-Control"], "public, max-age=31536000, immutable")
        self.assertTrue(stylesheet.endswith(sha256(css.encode()).hexdigest()[:24]))
        self.assertIn(asset_url("nunito-cyrillic.woff2"), css)
        self.assertIn(asset_url("nunito-latin.woff2"), css)
        self.assertEqual(cls.request(stylesheet, HTTP_IF_NONE_MATCH=headers["ETag"])[0], 304)
        self.assertEqual(cls.request(stylesheet, REQUEST_METHOD="HEAD")[2], "")
        self.assertEqual(cls.request("/static/main.css")[1]["Cache-Control"], "public, max-age=0, must-revalidate")
        self.assertEqual(cls.request("/static/main.css?v=old")[0], 404)

    @patch.dict("os.environ", {"DEAN_PRODUCT": "1", "DEAN_CAMPUS_LAB": "1"})
    def test_shared_shell_skips_unrelated_lab_assets_and_defers_railway_script(self):
        student = self.client("student-a")
        railway = f'<script src="{asset_url("railway.js")}"'
        for path in ("/login", "/", "/schedule", "/all-services", "/requests", "/assistant", "/lecture-library"):
            with self.subTest(path=path):
                client = self.client() if path == "/login" else student
                status, _, html = client.request(path)
                self.assertEqual(status, 200)
                self.assertNotIn(asset_url("campus-lab.css"), html)
                self.assertNotIn(asset_url("campus-lab.js"), html)
                self.assertIn(railway + (' data-chat-owner="' if path == "/" else ' defer>'), html)

    @patch.dict("os.environ", {"DEAN_PRODUCT": "1"})
    def test_staff_navigation_exposes_session_management_without_student_only_route(self):
        staff = self.client("staff")
        status, _, html = staff.request("/staff/support")
        self.assertEqual(status, 200)
        self.assertNotIn('href="/profile/access"', html)
        self.assertIn('href="/profile/sessions"', html)
        self.assertEqual(staff.request("/profile/sessions")[0], 200)

    def test_current_public_week_and_catalog_filters(self):
        with closing(connect(self.path)) as db:
            for monday in WEEKS:
                week = public_schedule.week_scope(SOURCE, GROUP, monday)
                public_sync.publish(db, public_sync.stage_sample(db, week))
        guest = self.client()
        base = "/universities/group?" + urlencode({"source": SOURCE, "group": GROUP, "q": "516-1", "city": "Томск"})
        with clock.at(datetime.fromisoformat("2026-11-18T08:00:00+00:00")):
            page = guest.request(base)[2]
            self.assertIn("Неделя с 16 ноября 2026", page)
            self.assertIn("16–22 ноября · Текущая", page)
            self.assertIn("23–29 ноября · Следующая", page)
            self.assertIn("q=516-1&amp;city=", page)
            self.assertIn("Неделя с 23 ноября 2026", guest.request(base + "&week=2026-11-23")[2])
        with clock.at(datetime.fromisoformat("2026-12-10T08:00:00+00:00")):
            self.assertIn("На текущую неделю данных нет", guest.request(base)[2])

    def test_schedule_return_context_and_rescheduled_notice(self):
        student, other, staff = (self.client(x) for x in ("student-a", "student-b", "staff"))
        lesson = "/schedule/" + seed_id("a1")
        params = {"date": self.today.isoformat(), "view": "list", "kind": "lesson",
                  "subject": "algorithms", "visibility": "study"}
        calendar = "/schedule?" + urlencode(params)
        html = student.request(calendar)[2]
        self.assertIn(lesson + "?", html)
        detail = student.request(lesson + "?" + urlencode(params))[2]
        self.assertIn("date=" + self.today.isoformat() + "&amp;view=list&amp;kind=lesson", detail)
        self.assertIn("subject=algorithms", detail)
        self.assertIn("visibility=study", detail)
        self.assertIn("Скрыть у себя", student.request(lesson.replace("/schedule/", "/schedule/raspay/lesson/"))[2])

        path = "/staff" + lesson
        fields = staff.fields(path, path)
        fields.update(starts=f"{self.today}T11:00", ends=f"{self.today}T12:30", room="ДЕМО · А-501")
        self.assertEqual(staff.request(path, fields)[0], 303)
        notices = student.request("/notifications?kind=schedule")[2]
        self.assertIn(lesson, notices)
        self.assertIn("ДЕМО · А-501", notices)
        self.assertIn("Было:", student.request("/schedule/changes")[2])
        self.assertIn("11:00", student.request(lesson)[2])
        self.assertNotIn("А-501", other.request("/schedule/changes")[2])
        self.assertEqual(other.request(lesson)[0], 404)

    def test_retry_after_response_loss_reuses_application_receipt(self):
        student = self.client("student-a")
        accept = {"HTTP_ACCEPT": "application/json"}
        previous_applications = self.db_one("SELECT COUNT(*) FROM application")
        previous_notices = self.db_one("SELECT COUNT(*) FROM notification WHERE kind='application'")
        inputs = student.fields(SERVICE, SERVICE)
        for purpose in ("", "аб", "ю" * 201):
            failed = student.request(SERVICE, {**inputs, "purpose": purpose}, **accept)
            self.assertEqual(failed[0], 422)
            self.assertIn("Цель получения", json.loads(failed[2])["message"])
        for purpose in ("арх", "ю" * 200):
            fields = {**student.fields(SERVICE, SERVICE), "purpose": purpose}
            # The first submission reached SQLite, but the client retries the same payload.
            accepted = student.request(SERVICE, fields, **accept)
            retry = student.request(SERVICE, fields, **accept)
            self.assertEqual((accepted[0], accepted[2]), (retry[0], retry[2]))
            self.assertEqual(accepted[0], 200)
            self.assertEqual(accepted[1]["Cache-Control"], "no-store")
            location = json.loads(retry[2])["location"]
            self.assertIn("Сотрудник возьмёт заявку в работу", student.request(location)[2])
        self.assertEqual(self.db_one("SELECT COUNT(*) FROM application"), previous_applications + 2)
        self.assertEqual(self.db_one("SELECT COUNT(*) FROM notification WHERE kind='application'"), previous_notices + 2)

    def test_two_certificates_previous_notice_filtered_read_and_pdf(self):
        student, other, staff = (self.client(x) for x in ("student-a", "student-b", "staff"))
        urls = []
        for purpose in ("Для архива проекта", "Для заявки на конкурс"):
            fields = {**student.fields(SERVICE, SERVICE), "purpose": purpose}
            urls.append(student.request(SERVICE, fields)[1]["Location"])
        listings = student.request("/requests")[2]
        for target, purpose in zip(urls, ("Для архива проекта", "Для заявки на конкурс")):
            self.assertIn(target, listings)
            self.assertIn("№ " + target.split("/")[-1][:8], listings)
            self.assertIn(purpose, listings)
        first = urls[0]
        self.assertEqual(staff.request("/staff" + first, staff.fields(first, "/staff" + first))[0], 303)
        change = "/staff" + first + "/needs_changes"
        self.assertEqual(staff.request(change, {**staff.fields(first, change), "note": "Уточни, какой архив."})[0], 303)
        self.assertIn("Что нужно исправить", student.request(first)[2])
        send = {**student.fields(first, first + "/save"), "purpose": "Для учебного архива проекта"}
        self.assertEqual(student.request(first + "/send", send)[0], 303)
        for _ in range(2):
            action = "/staff" + first
            self.assertEqual(staff.request(action, staff.fields(first, action))[0], 303)

        target = "/notifications?filter=unread&kind=application"
        alerts = student.request(target)[2]
        self.assertIn("Ранее · Заявка: требуется исправление", alerts)
        self.assertIn("Сейчас: Документ готов", alerts)
        self.assertIn(first + "#request-history", alerts)
        before = alerts.count('data-unread="true"')
        forms = Forms(alerts).forms
        read = next(path for path in forms if re.fullmatch(r"/notifications/[a-f0-9]{32}/read", path))
        status, headers, _ = student.request(read, forms[read])
        self.assertEqual(status, 303)
        location = headers["Location"]
        self.assertEqual(parse_qs(urlsplit(location).query)["kind"], ["application"])
        self.assertEqual(parse_qs(urlsplit(location).query)["filter"], ["unread"])
        updated = student.request(location)[2]
        self.assertEqual(updated.count('data-unread="true"'), before - 1)
        self.assertIn("data-restore-focus", updated)

        page = student.request(first)[2]
        pdf = re.search(r'(/documents/[a-f0-9]{32})\?download=1', page)[1]
        status, headers, payload = student.request(pdf + "?download=1")
        self.assertEqual((status, headers["Cache-Control"]), (200, "no-store"))
        self.assertTrue(payload.startswith(b"%PDF"))
        self.assertTrue(headers["Content-Disposition"].startswith("attachment;"))
        self.assertEqual(other.request(pdf)[0], 404)
        self.assertEqual(other.request(first)[0], 404)

    def test_shared_device_explanation_and_keyboard_target_on_login_error(self):
        guest = self.client()
        data = guest.fields("/login", "/login")
        wrong = guest.request("/login", {**data, "username": "student-a", "password": "incorrect"})
        self.assertEqual(wrong[0], 422)
        self.assertIn('aria-describedby="login-error" data-restore-focus', wrong[2])
        fields = {**guest.fields("/login", "/login"), "username": "student-a",
                  "password": self.passwords["student-a"], "remember": "1"}
        self.assertEqual(guest.request("/login", fields)[0], 303)
        self.assertIn("Вход действует на текущий сеанс", guest.request("/")[2])
        self.assertNotIn("Вход действует на текущий сеанс", guest.request("/")[2])

    def test_notifications_paginate_and_keep_filter_after_marking(self):
        student = self.client("student-a")
        with closing(connect(self.path)) as db:
            for number in range(15):
                services.notify(db, UNIVERSITY, seed_id("student-a"), "support", seed_id(f"ticket-{number}"),
                                f"test:mobile-notice:{number}", f"Ответ поддержки {number:02d}", "Проверка",
                                "2026-11-22T09:00:00+00:00")

        url = "/notifications?filter=unread&kind=support"
        first = student.request(url)[2]
        self.assertEqual(first.count("Ответ поддержки "), 12)
        self.assertIn('page=2#notification-feed" rel="next"', first)
        self.assertNotIn("Ответ поддержки 00", first)
        second = student.request(url + "&page=2")[2]
        self.assertEqual(second.count("Ответ поддержки "), 3)
        self.assertIn("Ответ поддержки 00", second)
        forms = Forms(second).forms
        read = next(path for path in forms if re.fullmatch(r"/notifications/[a-f0-9]{32}/read", path))
        status, headers, _ = student.request(read, forms[read])
        self.assertEqual(status, 303)
        location = headers["Location"]
        self.assertEqual(parse_qs(urlsplit(location).query)["page"], ["2"])
        self.assertIn("kind=support", location)
        self.assertEqual(student.request(location)[2].count("Ответ поддержки "), 2)
        status, headers, _ = student.request("/notifications/read-all", forms["/notifications/read-all"])
        self.assertEqual(status, 303)
        self.assertNotIn("page=2", headers["Location"])
        self.assertNotIn("Ответ поддержки ", student.request(url)[2])
