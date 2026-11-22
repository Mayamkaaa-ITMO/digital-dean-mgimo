from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from datetime import datetime, timedelta
from uuid import uuid4

from dean.clock import at
from dean.db import connect
from dean.fixtures import demo_id
from dean.seed import UNIVERSITY, seed_id

from .test_dean import DemoFixture


class AcademicProcessTest(DemoFixture):
    def submit(self, client, page, action, **values):
        return client.request(action, {**client.fields(page, action), **values})

    def test_retakes_scope_capacity_calendar_and_result(self):
        a, b, staff = (self.client(name) for name in ("student-a", "student-b", "staff"))
        did = demo_id(UNIVERSITY, "retake", "previous-practicum")
        sid = demo_id(UNIVERSITY, "retake", "slot-2")
        url, staff_url = "/retakes/" + did, "/staff/retakes/" + did
        slot_url = "/slots/" + sid
        action = slot_url + "/book"
        self.assertIn(url, a.request("/retakes")[2])
        self.assertEqual(b.request(url)[0], 404)
        self.assertEqual(b.request(slot_url)[0], 404)
        self.assertEqual(a.request(action, {**a.fields("/profile", "/logout"), "request_id": uuid4().hex,
                                           "version": "1"})[0], 409)
        self.assertEqual(self.submit(staff, staff_url, staff_url + "/admit", reason="Отчёт проверен.")[0], 303)
        second_debt = uuid4().hex
        with closing(connect(self.path)) as db:
            db.execute("UPDATE account SET group_id='demo-ai' WHERE username='student-b'")
            db.execute("""
                INSERT INTO learning_result (id,university_id,student_id,period_id,subject_id,kind,outcome)
                VALUES (?,?,?,'demo-previous','demo-practicum','credit','failed')
            """, (second_debt, UNIVERSITY, seed_id("student-b")))
            db.execute("INSERT INTO retake_debt (id,reason,conditions,admitted) VALUES (?,?,?,1)",
                       (second_debt, "Тестовый долг", "Допуск подтверждён"))
        a_fields, b_fields = (client.fields(slot_url, action) for client in (a, b))
        with ThreadPoolExecutor(max_workers=2) as pool:
            x = pool.submit(a.request, action, a_fields)
            y = pool.submit(b.request, action, b_fields)
            responses = [x.result(), y.result()]
        self.assertEqual(sorted(response[0] for response in responses), [303, 409])
        winner, winner_debt = (a, did) if responses[0][0] == 303 else (b, second_debt)
        payload = a_fields if winner is a else b_fields
        booking = next(r[1]["Location"] for r in responses if r[0] == 303)
        self.assertEqual(winner.request(action, payload)[1]["Location"], booking)
        self.assertIsNone(self.db_one("SELECT closed_at FROM retake_debt WHERE id=?", (winner_debt,)))
        date = (self.today + timedelta(days=2)).isoformat()
        self.assertIn(booking, winner.request("/schedule?date=" + date)[2])
        self.assertEqual(self.submit(winner, booking, booking + "/cancel")[0], 303)
        reservation = self.submit(winner, slot_url, action)[1]["Location"].rsplit("/", 1)[-1]
        after = datetime.fromisoformat(self.db_one("SELECT ends_at FROM reservation WHERE id=?", (reservation,)))
        manage = "/staff/retakes/" + winner_debt
        with at(after):
            operation = manage + "/result"
            fields = {**staff.fields(manage, operation), "result": "passed", "reason": "Практикум завершён."}
            self.assertEqual(staff.request(operation, fields)[0], 303)
            self.assertEqual(staff.request(operation, fields)[0], 303)
        self.assertIn("Задолженность закрыта", self.read_after_restart(winner, "/retakes/" + winner_debt))
        self.assertIn("Пересдача сдана", winner.request("/notifications")[2])
        self.assertEqual(self.db_one("SELECT COUNT(*) FROM retake_booking WHERE result='passed'"), 1)

    def test_individual_plan_validation_review_and_immutable_approval(self):
        a, b, staff = (self.client(name) for name in ("student-a", "student-b", "staff"))
        plan = "/curriculum"
        elective = self.db_one("""
            SELECT c.id FROM curriculum_item c JOIN curriculum_block_item i ON i.item_id=c.id
            JOIN curriculum_block b ON b.id=i.block_id
            WHERE c.program_id='ai' AND c.period_id=? AND b.rule='one' ORDER BY c.title LIMIT 1
        """, (self.period,))
        block = self.db_one("SELECT id FROM curriculum_block WHERE program_id='ai' AND period_id=? AND rule='credits'",
                            (self.period,))
        credit = self.db_one("""
            SELECT c.id FROM curriculum_item c JOIN curriculum_block_item i ON i.item_id=c.id
            WHERE i.block_id=? AND c.credits=4
        """, (block,))
        action = "/curriculum/block/" + block
        fields = a.fields(plan, action)
        self.assertEqual(a.request(action, fields)[0], 422)
        self.assertEqual(b.request(action, {**b.fields("/profile", "/logout"), "request_id": uuid4().hex,
                                          f"item_{credit}": "1"})[0], 404)
        student = seed_id("student-a")
        with closing(connect(self.path)) as db:
            db.execute("UPDATE learning_result SET outcome='failed' WHERE student_id=? AND subject_id='english' "
                       "AND period_id='demo-previous'", (student,))
        self.assertEqual(a.request(action, {**fields, f"item_{credit}": "1"})[0], 422)
        with closing(connect(self.path)) as db:
            db.execute("UPDATE learning_result SET outcome='passed' WHERE student_id=? AND subject_id='english' "
                       "AND period_id='demo-previous'", (student,))
        self.assertEqual(a.request(action, {**fields, f"item_{credit}": "1"})[0], 303)
        self.assertEqual(a.request(action, {**fields, f"item_{credit}": "1"})[0], 303)
        service = "/services/individual-plan"
        self.assertEqual(self.submit(a, service, service, details="План на семестр.")[0], 422)
        self.assertEqual(self.submit(a, plan, f"/curriculum/{elective}/choose")[0], 303)
        draft_fields = {**a.fields(service, service), "details": "План на семестр."}
        url = a.request(service + "/draft", draft_fields)[1]["Location"]
        self.assertEqual(a.request(url + "/send", {**a.fields(url, url + "/save"), "details": "План на семестр."})[0], 303)
        review = "/staff" + url
        self.assertEqual(self.submit(staff, url, review)[0], 303)
        self.assertEqual(self.submit(staff, url, review + "/needs_changes", note="Опиши цель проекта.")[0], 303)
        self.assertEqual(a.request(url + "/send", {**a.fields(url, url + "/save"), "details": "Улучшенный план учебного проекта."})[0], 303)
        self.assertEqual(self.submit(staff, url, review)[0], 303)
        fields = {**staff.fields(url, review), "note": "План согласован."}
        self.assertEqual(staff.request(review, fields)[0], 303)
        self.assertEqual(staff.request(review, fields)[0], 303)
        self.assertEqual(self.db_one("SELECT COUNT(*) FROM individual_plan_approval"), 1)
        self.assertIn("Учебный проект", a.request(plan)[2])
        self.assertNotIn(url, b.request(plan)[2])

    def test_attendance_correction_preserves_history(self):
        a, b, staff = (self.client(name) for name in ("student-a", "student-b", "staff"))
        eid = seed_id("a1")
        lesson = "/schedule/" + eid
        url = "/staff/attendance/" + eid
        end = datetime.fromisoformat(self.db_one("SELECT ends_at FROM schedule_event WHERE id=?", (eid,)))
        with at(end):
            self.assertEqual(self.submit(staff, url, url, state="absent")[0], 303)
            self.assertEqual(self.submit(staff, url, url, state="present")[0], 422)
            self.assertEqual(self.submit(staff, url, url, state="present", reason="Проверен журнал")[0], 303)
        self.assertIn("Присутствовал", a.request(lesson)[2])
        self.assertEqual(b.request(lesson)[0], 404)
        self.assertIn("Проверен журнал", staff.request(url)[2])
        self.assertEqual(self.db_one("SELECT COUNT(*) FROM lesson_attendance_history"), 2)
