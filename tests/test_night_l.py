from contextlib import closing

from dean import public_schedule, public_sync
from dean.db import connect
from tests.test_dean import DemoFixture

FIRST = "spbpu-ruz|125/45492"
OTHER = "susu-timetable|ke-102"
START = "2026-11-16"


def choose(client, selection, **changes):
    path = "/schedule/context"
    fields = client.fields(path, path)
    return client.request(path, {**fields, "selection": selection, "action": "view", **changes})


class PrivateStudyTest(DemoFixture):
    def publish_samples(self):
        with closing(connect(self.path)) as db:
            for value in (FIRST, OTHER):
                scope = public_schedule.week_scope(*value.split("|"), START)
                public_sync.publish(db, public_sync.stage_sample(db, scope))

    def test_main_favorite_and_late_context_change(self):
        self.publish_samples()
        student = self.client("student-a")
        path = "/schedule/context"
        old = student.fields(path, path)
        self.assertEqual(choose(student, FIRST, action="main", focus_day=START)[0], 303)
        self.assertEqual(student.request(path, {**old, "selection": OTHER})[0], 409)
        self.assertIn("Методы принятия решений", student.request("/schedule")[2])
        self.assertEqual(choose(student, FIRST, action="favorite")[0], 303)
        self.assertEqual(choose(student, OTHER)[0], 303)
        self.assertIn("КЭ-102", self.read_after_restart(student, "/"))
        second_login = self.client("student-a")
        self.assertIn("КЭ-102", second_login.request("/")[2])
        self.assertEqual(choose(second_login, "", action="return")[0], 303)
        self.assertIn("5140201/50302", second_login.request("/")[2])
        self.assertEqual(self.db_one("SELECT count(*) FROM study_group_option WHERE favorite=1"), 1)
        self.assertIn("Учебный стенд · вымышленные данные", second_login.request("/grades")[2])

    def test_adopt_notes_results_restore_links_and_search_scope(self):
        self.publish_samples()
        first, second = self.client("student-a"), self.client("student-b")
        choose(first, FIRST)
        choose(second, FIRST)
        index = "/study/subjects"
        subject = "Методы принятия решений в условиях неопределенности"
        forms = first.fields(index, index)
        response = first.request(index, {**forms, "title": "", "source_title": subject})
        self.assertEqual(response[0], 303)
        detail = response[1]["Location"]
        self.assertEqual(first.request(index, {**forms, "title": "", "source_title": subject})[1]["Location"], detail)
        self.assertEqual(second.request(detail)[0], 404)
        action = detail + "/save"
        old = first.fields(detail, action)
        self.assertEqual(first.request(action, {**old, "title": subject, "note": "Векторный полином <x>"})[0], 303)
        self.assertIn("Векторный полином &lt;x&gt;", self.read_after_restart(first, detail))
        self.assertNotIn("Векторный полином", second.request("/search?q=полином")[2])
        self.assertIn("Векторный полином", first.request("/search?q=полином")[2])
        result = detail + "/result"
        form = first.fields(detail, result)
        request = {**form, "coursework": "0", "exam_points": "80", "weight": "50", "target": "30"}
        self.assertEqual(first.request(result, request)[0], 303)
        self.assertIn("Мой расчёт: 40 из 100", first.request(detail)[2])
        self.assertEqual(first.request(action, {**old, "request_id": form["request_id"], "title": subject})[0], 409)
        unlink = detail + "/unlink"
        self.assertEqual(first.request(unlink, first.fields(detail, unlink))[0], 303)
        self.assertIn("Векторный полином", first.request(detail)[2])
        self.assertIn("Мой расчёт: 40 из 100", first.request(detail)[2])
        self.assertNotIn(subject, second.request(index)[2].split("Из расписания", 1)[0])
