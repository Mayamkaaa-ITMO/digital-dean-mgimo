import re
import zlib
from contextlib import closing
from datetime import datetime
from uuid import uuid4

from dean.clock import at
from dean.db import connect
from dean.fixtures import demo_id
from dean.reservations import fixture_id
from dean.seed import UNIVERSITY

from .test_dean import DemoFixture


def pdf_text(pdf):
    lines = []
    for match in re.finditer(rb"/Length (\d+) /Filter /FlateDecode [^\n]*\nstream\n", pdf):
        start = match.end()
        decoded = zlib.decompress(pdf[start:start + int(match[1])])
        for show in re.findall(rb"Tm <([0-9A-F]+)> Tj ET", decoded):
            lines.append(bytes.fromhex(show.decode()).decode("utf-16be"))
    return "\n".join(lines)


class CareerProcessTest(DemoFixture):
    def post(self, client, page, action, **values):
        return client.request(action, {**client.fields(page, action), **values})

    def test_practice_application_diary_two_report_versions_and_assigned_supervisor(self):
        a, b, staff = (self.client(login) for login in ("student-a", "student-b", "staff"))
        oid = self.db_one("SELECT id FROM opportunity WHERE title LIKE 'Учебная практика%'")
        base = "/services/practice-enrolment"
        self.assertIn(base + "?practice=" + oid, a.request("/opportunities/" + oid)[2])
        response = self.post(a, base, base, practice=oid, goal="Сравнить две модели синтетических данных.")
        self.assertEqual(response[0], 303)
        url = response[1]["Location"]
        action = "/staff" + url
        self.assertEqual(self.post(staff, url, action)[0], 303)
        self.assertEqual(self.post(staff, url, action, note="Практика одобрена, отчёт с результатами.")[0], 303)
        pid = self.db_one("SELECT id FROM practice_case")
        path = "/practice/" + pid
        manage = "/staff" + path
        self.assertIn(path, a.request(url)[2])
        self.assertEqual(b.request(path)[0], 404)
        staff2 = uuid4().hex
        with closing(connect(self.path)) as db:
            db.execute("""
                INSERT INTO account (id,username,password_hash,full_name,role,university_id)
                SELECT ?,'staff-substitute',password_hash,'Демо сотрудник','staff',university_id
                FROM account WHERE username='staff'
            """, (staff2,))
            db.execute("INSERT INTO staff_group VALUES (?,?,'demo-ai')", (UNIVERSITY, staff2))
        outsider = self.client()
        self.assertEqual(outsider.login("staff-substitute", self.passwords["staff"])[0], 303)
        self.assertEqual(outsider.request(manage)[0], 404)
        report = path + "/report"
        self.assertEqual(self.post(a, path, report, body="Предварительный отчёт о работе.")[0], 422)
        self.assertEqual(self.post(a, path, path + "/diary", body="Собрал синтетическую обучающую выборку.")[0], 303)
        first = {**a.fields(path, report), "body": "Первая версия отчёта о работе."}
        self.assertEqual(a.request(report, first)[0], 303)
        self.assertEqual(a.request(report, first)[0], 303)
        self.assertEqual(self.post(staff, manage, manage + "/review", state="changes",
                                   feedback="Добавь критерий сравнения.")[0], 303)
        self.assertEqual(self.post(a, path, report, body="Вторая версия отчёта с критериями сравнения.")[0], 303)
        self.assertEqual(self.post(staff, manage, manage + "/review", state="passed", feedback="Демо-результат принят.")[0], 303)
        text = self.read_after_restart(a, path)
        for substring in ("Практика зачтена", "Первая версия отчёта", "Вторая версия отчёта", "Добавь критерий"):
            self.assertIn(substring, text)
        self.assertEqual(self.db_one("SELECT COUNT(*) FROM practice_report"), 2)
        self.assertIn("Практика зачтена", a.request("/notifications")[2])

    def test_confirmation_snapshot_and_private_selective_resume(self):
        a, b, staff = (self.client(login) for login in ("student-a", "student-b", "staff"))
        create = "/portfolio/new"
        chosen = self.post(a, create, create, title="Выбранное достижение", kind="achievement",
                           content="Описание выбранной работы.")[1]["Location"]
        hidden = self.post(a, create, create, title="Личный невыбранный проект", kind="project",
                           content="Этого текста не будет в файле.")[1]["Location"]
        eid = chosen.rsplit("/", 1)[-1]
        self.assertIn("Добавлено студентом", a.request(chosen)[2])
        self.assertEqual(self.post(a, chosen, chosen + "/verify")[0], 303)
        action = "/staff/achievements/" + eid
        self.assertEqual(self.post(staff, "/staff/achievements", action, state="accepted", note="Проверены данные.")[0], 303)
        self.assertIn("Подтверждено сотрудником", a.request(chosen)[2])
        self.assertEqual(b.request(chosen)[0], 404)
        service = "/services/enhanced-stipend"
        forms = self.post(a, service, service, achievement=eid, details="Прошу назначение по достижению.")
        self.assertEqual(forms[0], 303)
        self.assertIn("подтверждено сотрудником на момент подачи", a.request(forms[1]["Location"])[2])
        resume = "/portfolio/resume"
        fields = {**a.fields(resume, resume), f"item_{eid}": "1"}
        exported = a.request(resume, fields)
        self.assertEqual(exported[0], 303)
        self.assertEqual(a.request(resume, fields)[1]["Location"], exported[1]["Location"])
        downloaded = exported[1]["Location"] + "/download"
        self.assertEqual(b.request(downloaded)[0], 404)
        self.assertEqual(staff.request(downloaded)[0], 403)
        page = pdf_text(a.request(downloaded)[2])
        self.assertIn("Выбранное достижение", page)
        self.assertNotIn("Личный невыбранный проект", page)
        self.assertNotIn("Мира Яроветова", page)
        self.assertNotIn(hidden, page)
        self.assertEqual(self.db_one("SELECT COUNT(*) FROM resume_document"), 1)

    def test_activity_confirmation_records_one_verified_achievement(self):
        a, staff = self.client("student-a"), self.client("staff")
        sid = fixture_id(UNIVERSITY, "event-lab:0")
        slot = "/slots/" + sid
        booked = self.post(a, slot, slot + "/book", ack="1")
        self.assertEqual(booked[0], 303)
        rid = booked[1]["Location"].rsplit("/", 1)[-1]
        manage = "/staff" + slot
        action = manage + "/attendance/" + rid
        finish = datetime.fromisoformat(self.db_one("SELECT ends_at FROM reservation WHERE id=?", (rid,)))
        with at(finish):
            fields = staff.fields(manage, action)
            self.assertEqual(staff.request(action, fields)[0], 303)
            self.assertEqual(staff.request(action, {**fields, "request_id": uuid4().hex})[0], 303)
        self.assertEqual(self.db_one("SELECT COUNT(*) FROM portfolio_verification WHERE reservation_id=?", (rid,)), 1)
        self.assertIn("Подтверждено сотрудником", a.request("/portfolio")[2])

    def test_course_selection_enrollment_and_progress_are_personal(self):
        a, b = self.client("student-a"), self.client("student-b")
        cid = demo_id(UNIVERSITY, "course", "short-study")
        lid = demo_id(UNIVERSITY, "lesson", f"{cid}:1")
        second = demo_id(UNIVERSITY, "lesson", f"{cid}:2")
        path = "/courses/" + cid
        lesson = path + "/lessons/" + lid
        self.assertEqual(a.request(lesson)[0], 404)
        self.assertEqual(self.post(a, path, path + "/favorite")[0], 303)
        self.assertIn(path, a.request("/courses?favorites=1")[2])
        self.assertNotIn(path, b.request("/courses?favorites=1")[2])
        self.assertEqual(self.post(a, path, path + "/enroll")[0], 303)
        self.assertEqual(self.post(a, lesson, lesson + "/finish")[0], 303)
        self.assertEqual(b.request(lesson)[0], 404)
        second_path = path + "/lessons/" + second
        self.assertEqual(self.post(a, second_path, second_path + "/finish")[0], 303)
        self.assertIn("2 из 2", a.request(path)[2])
        self.assertIn("Курс завершён", a.request("/notifications")[2])
