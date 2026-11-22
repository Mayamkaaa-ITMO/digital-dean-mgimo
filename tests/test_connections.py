from datetime import datetime, time, timedelta
from urllib.parse import urlencode
from zoneinfo import ZoneInfo

from dean.clock import at

from .test_dean import DemoFixture


class ConnectedExperienceTest(DemoFixture):
    def local_time(self, hour, minute=0):
        return datetime.combine(self.today, time(hour, minute), ZoneInfo("Europe/Moscow"))

    def send(self, client, page, action, **values):
        return client.request(action, {**client.fields(page, action), **values})

    def test_dashboard_day_states_and_search_scope(self):
        a, b = self.client("student-a"), self.client("student-b")
        with at(self.local_time(10, 15)):
            html = a.request("/")[2]
            self.assertIn("Идёт сейчас", html)
            self.assertIn("Следующая пара", html)
            self.assertIn('href="/requests?mode=catalog"', html)
            self.assertNotIn("Дела и обновления", html)
            task = self.send(a, "/tasks/new", "/tasks/new", title="Приватная проверочная задача",
                             description="Учебный маркер", due=f"{self.today}T11:30")
            self.assertEqual(task[0], 303)
            task_path = task[1]["Location"]
            self.assertIn(task_path, a.request("/")[2])
            application, _ = self.submit(a, "Приватный маркер для поиска")
            find = "/search?" + urlencode({"q": "ПРИВАТНЫЙ МАРКЕР", "kind": "application"})
            self.assertIn(application, a.request(find)[2])
            self.assertIn("Найдено: 0", b.request(find)[2])
            find = "/search?" + urlencode({"q": "проверочная задача", "kind": "task"})
            self.assertIn(task_path, a.request(find)[2])
            self.assertIn("Найдено: 0", b.request(find)[2])
        with at(self.local_time(18)):
            html = a.request("/")[2]
            self.assertIn("На сегодня занятия закончились.", html)
            self.assertIn("Следующая пара · ", html)
            self.assertIn("Просрочено ·", html)
            self.assertIn(task_path, html)

    def test_optional_reminders_are_idempotent_and_update_on_edit_and_completion(self):
        a, b = self.client("student-a"), self.client("student-b")
        with at(self.local_time(8)):
            task = self.send(a, "/tasks/new", "/tasks/new", title="Подготовить отчёт", due=f"{self.today}T10:00")
            self.assertEqual(task[0], 303)
            task_path = task[1]["Location"]
            event = self.send(a, "/events/new", "/events/new", title="Личная встреча",
                              starts=f"{self.today}T14:00", ends=f"{self.today}T15:00")
            self.assertEqual(event[0], 303)
            event_path = event[1]["Location"]
            for _ in range(2):
                self.assertIn("Скоро срок задачи", a.request("/notifications?kind=task")[2])
            self.assertEqual(self.db_one("SELECT count(*) FROM notification WHERE kind='task'"), 1)
            self.assertIn("14:00", a.request("/notifications?kind=event")[2])
            combined = a.request("/notifications?kind=deadlines")[2]
            self.assertIn("Скоро срок задачи", combined)
            self.assertIn("Скоро личное событие", combined)
            self.assertEqual(self.send(a, event_path + "/edit", event_path + "/edit", title="Личная встреча",
                                      starts=f"{self.today}T16:00", ends=f"{self.today}T17:00")[0], 303)
            notice = a.request("/notifications?kind=event")[2]
            self.assertIn("16:00", notice)
            self.assertNotIn("14:00", notice)
            self.assertEqual(self.db_one("SELECT count(*) FROM notification WHERE kind='event' AND active=1"), 1)
            self.assertEqual(self.send(a, event_path, event_path + "/delete")[0], 303)
            self.assertNotIn("Скоро личное событие", a.request("/notifications?kind=event")[2])
            self.assertEqual(self.send(a, task_path, task_path + "/complete")[0], 303)
            self.assertNotIn("Скоро срок задачи", a.request("/notifications?kind=task")[2])

            self.submit(a)
            csrf = a.fields("/notifications", "/logout")["csrf"]
            self.assertEqual(a.request("/notifications/settings", {"csrf": csrf})[0], 303)
            all_items = a.request("/notifications")[2]
            self.assertNotIn("Приближается экзамен", all_items)
            self.assertIn("Заявка отправлена", all_items)
            self.assertEqual(self.send(a, "/notifications", "/notifications/read-all")[0], 303)
            self.assertNotIn("Заявка отправлена", a.request("/notifications?filter=unread")[2])
            self.assertIn("Заявка отправлена", b.request("/notifications?filter=unread")[2])

    def test_delivered_reminder_remains_in_history_after_deadline(self):
        a=self.client("student-a")
        with at(self.local_time(8)):
            result=self.send(a,"/tasks/new","/tasks/new",title="Напоминание после срока",due=f"{self.today}T10:00")
            self.assertEqual(result[0],303)
            self.assertIn("Скоро срок задачи",a.request("/notifications?kind=task")[2])
        with at(self.local_time(11)):
            self.assertIn("Скоро срок задачи",a.request("/notifications?kind=task")[2])
