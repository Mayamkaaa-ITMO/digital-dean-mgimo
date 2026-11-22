from datetime import datetime, timedelta
from urllib.parse import urlencode

from dean.seed import seed_id

from .test_dean import DemoFixture


class StudyTest(DemoFixture):
    def submit_form(self, client, page, action, **values):
        return client.request(action, {**client.fields(page, action), **values})

    def test_task_filters_subtask_notes_and_scope(self):
        a, b = self.client("student-a"), self.client("student-b")
        result = self.submit_form(a, "/tasks/new", "/tasks/new", title="Повторить JOIN",
                                  subject="databases", priority="high", description="Задания из конспекта")
        self.assertEqual(result[0], 303)
        url = result[1]["Location"]
        self.assertIn("Без срока", a.request(url)[2])
        add = url + "/subtasks"
        self.assertEqual(self.submit_form(a, url, add, title="Внешнее соединение")[0], 303)
        sid = self.db_one("SELECT id FROM subtask")
        self.assertEqual(self.submit_form(a, url, f"{add}/{sid}/complete")[0], 303)
        self.assertIn("Готово 1 из 1", a.request(url)[2])
        note_path = "/notes/task/" + url.split("/")[-1]
        note = self.submit_form(a, url, note_path, body="<em>Запомнить JOIN</em>")
        private = note[1]["Location"]
        self.assertEqual(note[0], 303)
        html = a.request(url)[2]
        self.assertIn("&lt;em&gt;Запомнить JOIN&lt;/em&gt;", html)
        self.assertEqual(b.request(private)[0], 404)
        self.assertEqual(b.request(url)[0], 404)
        token = b.fields("/profile", "/logout")["csrf"]
        self.assertEqual(b.request(private, {"csrf": token, "request_id": "f"*32, "body": "Подмена"})[0], 404)
        self.assertIn(url, a.request("/tasks?" + urlencode({"q": "join", "priority": "high", "subject": "databases"}))[2])
        self.assertNotIn(url, b.request("/tasks")[2])
        self.assertEqual(self.submit_form(a, url + "/edit", url + "/edit", title="Повторить SQL",
                                          subject="databases", priority="normal")[0], 303)
        self.assertEqual(self.submit_form(a, url, url + "/complete")[0], 303)
        a = self.client("student-a")
        self.assertIn("Повторить SQL", a.request("/tasks?state=done")[2])
        self.assertEqual(self.submit_form(a, url, url + "/delete")[0], 303)
        self.assertEqual(a.request(url)[0], 404)
        self.assertEqual(a.request(private)[0], 404)

    def test_calendar_overlap_recurrence_skip_all_day_and_privacy(self):
        a, b = self.client("student-a"), self.client("student-b")
        form = "/events/new"
        today = self.today.isoformat()
        repeat = (self.today + timedelta(days=14)).isoformat()

        def create(title, start, end, **other):
            data = {"title": title, "starts": f"{today}T{start}", "ends": f"{today}T{end}", **other}
            response = self.submit_form(a, form, form, **data)
            self.assertEqual(response[0], 303)
            return response[1]["Location"]

        equal = create("Сразу после пары", "11:30", "12:00")
        dashboard = a.request(f"/schedule?date={today}")[2]
        self.assertIn(equal, dashboard)
        self.assertNotIn("Пересечение", dashboard)
        series = create("Пересечение с парой", "10:30", "11:00", repeat_until=repeat)
        page = a.request(f"/schedule?date={today}")[2]
        self.assertIn(series, page)
        self.assertEqual(page.count(">Пересечение<"), 2)
        response = self.submit_form(a, form, form, title="Событие на день", all_day="1",
                                    first_day=repeat, last_day=repeat)
        self.assertEqual(response[0], 303)
        self.assertIn("Весь день", a.request(f"/schedule?date={repeat}&kind=event")[2])
        skip = self.submit_form(a, series + "?occurrence=" + repeat, series + "/skip", day=repeat)
        self.assertEqual(skip[0], 303)
        later = a.request(f"/schedule?date={repeat}&kind=event")[2]
        self.assertNotIn("Пересечение с парой", later)
        self.assertIn("Событие на день", later)
        self.assertEqual(b.request(series)[0], 404)
        self.assertNotIn("Событие на день", b.request(f"/schedule?date={repeat}")[2])
        too_long = (self.today + timedelta(weeks=26)).isoformat()
        response = self.submit_form(a, form, form, title="Длинная серия", starts=f"{today}T10:00",
                                    ends=f"{today}T11:00", repeat_until=too_long)
        self.assertEqual(response[0], 422)

    def test_task_completion_warns_and_accepts_explicit_subtask_confirmation(self):
        student = self.client("student-a")
        result = self.submit_form(student, "/tasks/new", "/tasks/new", title="Разобрать примеры")
        url = result[1]["Location"]
        complete = url + "/complete"
        self.assertEqual(self.submit_form(student, url, url + "/subtasks", title="Проверить второй пример")[0], 303)
        self.assertIn("Завершить оставшиеся подзадачи вместе с задачей", student.request(url)[2])
        fields = student.fields(url, complete)
        response = student.request(complete, fields)
        self.assertEqual(response[0], 422)
        self.assertIn("Заверши открытые подзадачи", response[2])
        self.assertEqual(self.db_one("SELECT done FROM subtask"), 0)
        self.assertEqual(student.request(complete, fields | {"complete_subtasks": "1"})[0], 303)
        self.assertIn("Готово 1 из 1", student.request(url)[2])
        self.assertIn("Вернуть в работу", student.request(url)[2])
        subtask = self.db_one("SELECT id FROM subtask")
        self.assertEqual(self.submit_form(student, url, f"{url}/subtasks/{subtask}/reopen")[0], 303)
        self.assertIn("Готово 0 из 1", student.request(url)[2])
        self.assertIn("Завершить задачу", student.request(url)[2])

    def test_grade_calculator_plan_staff_history_and_access(self):
        a, b, staff = (self.client(name) for name in ("student-a", "student-b", "staff"))
        rid = seed_id(f"student-a:databases:{self.period}")
        path = f"/grades/{rid}"
        self.assertIn("Предстоит", a.request(path)[2])
        self.assertIn("42 / 50", a.request(path)[2])
        self.assertEqual(self.submit_form(a, path, path + "/scenario", target="85", exam_points="27")[0], 303)
        html = a.request(path)[2]
        self.assertIn("86 / 100", html)
        self.assertIn("27 / 30", html)
        self.assertIsNone(self.db_one("SELECT score FROM learning_result WHERE id=?", (rid,)))
        self.assertEqual(self.submit_form(a, path, path + "/scenario", target="100", exam_points="0")[0], 303)
        self.assertIn("Цель 100 недостижима", a.request(path)[2])
        plan_fields = a.fields(path, path + "/plan")
        self.assertEqual(a.request(path + "/plan", plan_fields)[0], 303)
        self.assertEqual(a.request(path + "/plan", plan_fields)[0], 303)
        self.assertEqual(self.db_one("SELECT count(*) FROM task"), 3)
        self.assertIn("Собрать темы и материалы", self.client("student-a").request(path)[2])
        self.assertEqual(b.request(path)[0], 404)
        other = seed_id(f"student-b:circuits:{self.period}")
        self.assertEqual(staff.request(f"/staff/grades/{other}")[0], 404)
        moved = (self.today + timedelta(days=5)).isoformat() + "T15:00"
        staff_page = f"/staff/grades/{rid}"
        self.assertEqual(self.submit_form(staff, staff_page, staff_page, score="", exam_at=moved)[0], 303)
        self.assertIn("Пересмотри сроки задач", a.request(path)[2])
        self.assertIn("Изменилась дата экзамена", a.request("/notifications")[2])
        self.assertEqual(self.db_one("SELECT count(*) FROM grade_revision WHERE result_id=?", (rid,)), 1)
        self.assertEqual(self.db_one("SELECT count(*) FROM task"), 3)

    def test_note_remains_linked_after_lesson_update(self):
        a, b, staff = (self.client(name) for name in ("student-a", "student-b", "staff"))
        eid = seed_id("a1")
        path = f"/schedule/{eid}"
        action = f"/notes/lesson/{eid}"
        self.assertEqual(self.submit_form(a, path, action, body="Личная заметка к семинару")[0], 303)
        staff_path = "/staff" + path
        response = self.submit_form(staff, staff_path, staff_path, starts=f"{self.today}T12:00",
                                    ends=f"{self.today}T12:45", room="ДЕМО · А-315")
        self.assertEqual(response[0], 303)
        self.assertIn("Личная заметка к семинару", a.request(path)[2])
        self.assertEqual(b.request(path)[0], 404)
