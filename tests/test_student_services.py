from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from dean.clock import at
from dean.curriculum import stable_id
from dean.reservations import fixture_id
from dean.seed import UNIVERSITY, seed_id

from .test_dean import DemoFixture


class StudentServicesTest(DemoFixture):
    def send(self, client, page, action, **data):
        return client.request(action, {**client.fields(page, action), **data})

    def test_activity_filters_registration_and_organizer_list(self):
        a, b, staff = (self.client(name) for name in ("student-a", "student-b", "staff"))
        self.assertIn("Экскурсия в студенческую лабораторию", a.request("/activities")[2])
        from urllib.parse import urlencode
        filtered = a.request("/activities?" + urlencode({"category": "Сообщество"}))[2]
        self.assertIn("Встреча проектных команд", filtered)
        self.assertNotIn("Экскурсия", filtered)
        sid = fixture_id(UNIVERSITY, "event-lab:0")
        page = f"/slots/{sid}"
        response = self.send(a, page, page + "/book", ack="1")
        self.assertEqual(response[0], 303)
        booking = response[1]["Location"]
        manage = f"/staff/slots/{sid}"
        self.assertIn("Мира Яроветова", staff.request(manage)[2])
        self.assertEqual(b.request(manage)[0], 403)
        day = self.today + timedelta(days=1)
        self.assertIn(booking, a.request(f"/schedule?date={day}")[2])
        self.assertIn("Демонстрационная запись подтверждена", a.request("/notifications")[2])
        self.assertEqual(self.send(a, booking, booking + "/cancel")[0], 303)
        self.assertNotIn("Мира Яроветова", staff.request(manage)[2])

    def test_elective_has_program_scope_time_window_and_one_selection(self):
        a, b = self.client("student-a"), self.client("student-b")
        head = a.request("/curriculum")[2]
        self.assertIn("Зачтено кредитов", head)
        self.assertIn("Введение в анализ данных", head)
        self.assertNotIn("Моделирование электрических систем", head)
        pick = f"/curriculum/{stable_id(UNIVERSITY, f'ai:{self.period}:data')}/choose"
        other = f"/curriculum/{stable_id(UNIVERSITY, f'ai:{self.period}:projects')}/choose"
        self.assertEqual(self.send(a, "/curriculum", pick)[0], 303)
        self.assertIn(">Выбран<", a.request("/curriculum")[2])
        self.assertEqual(self.send(a, "/curriculum", other)[0], 303)
        self.assertEqual(self.db_one("SELECT count(*) FROM curriculum_choice"), 1)
        csrf = b.fields("/profile", "/logout")["csrf"]
        self.assertEqual(b.request(pick, {"csrf": csrf, "request_id": "a"*32})[0], 404)
        stale = a.fields("/curriculum", pick)
        future = datetime.combine(self.today+timedelta(days=17), datetime.min.time(), ZoneInfo("Europe/Moscow"))
        with at(future):
            self.assertEqual(a.request(pick, stale)[0], 409)

    def test_library_distinguishes_copies_and_loans_with_private_renewal(self):
        a, b, staff = (self.client(name) for name in ("student-a", "student-b", "staff"))
        self.assertIn("Экземпляров в выдаче нет", a.request("/library")[2])
        copy = self.db_one("SELECT id FROM library_copy ORDER BY label LIMIT 1")
        action = "/staff/library/issue"
        result = self.send(staff, "/staff/library", action, student=seed_id("student-b"), copy=copy)
        self.assertEqual(result[0], 404)
        result = self.send(staff, "/staff/library", action, student=seed_id("student-a"), copy=copy)
        self.assertEqual(result[0], 303)
        url = result[1]["Location"]
        own = url.removeprefix("/staff")
        self.assertIn(own, a.request("/library/mine")[2])
        self.assertEqual(b.request(own)[0], 404)
        self.assertIn("Книга выдана", a.request("/notifications?kind=library")[2])
        due = self.db_one("SELECT due_at FROM library_loan")
        fields = a.fields(own, own + "/renew")
        self.assertEqual(a.request(own + "/renew", fields)[0], 303)
        self.assertEqual(a.request(own + "/renew", fields)[0], 303)
        self.assertEqual(datetime.fromisoformat(self.db_one("SELECT due_at FROM library_loan"))
                         - datetime.fromisoformat(due), timedelta(days=7))
        self.assertEqual(a.request(own + "/renew", {**fields, "request_id": "b"*32})[0], 409)
        self.assertEqual(self.send(staff, url, url + "/return")[0], 303)
        self.assertIn("Возвращена", a.request(own)[2])

    def test_library_reminder_updates_after_extension_and_return(self):
        a, staff = self.client("student-a"), self.client("staff")
        copy = self.db_one("SELECT id FROM library_copy ORDER BY label LIMIT 1")
        created = self.send(staff, "/staff/library", "/staff/library/issue",
                            student=seed_id("student-a"), copy=copy)
        self.assertEqual(created[0], 303)
        staff_path = created[1]["Location"]
        owned_path = staff_path.removeprefix("/staff")
        due = datetime.fromisoformat(self.db_one("SELECT due_at FROM library_loan"))
        with at(due - timedelta(days=1)):
            self.assertIn("Скоро вернуть книгу", a.request("/notifications")[2])
            self.assertEqual(self.send(a, owned_path, owned_path + "/renew")[0], 303)
            self.assertNotIn("Скоро вернуть книгу", a.request("/notifications")[2])
        with at(due + timedelta(days=6)):
            self.assertIn("Скоро вернуть книгу", a.request("/notifications")[2])
            self.assertEqual(self.send(staff, staff_path, staff_path + "/return")[0], 303)
            self.assertNotIn("Скоро вернуть книгу", a.request("/notifications")[2])

    def test_portfolio_and_manual_track_are_private(self):
        a, b = self.client("student-a"), self.client("student-b")
        form = "/portfolio/new"
        self.assertEqual(self.send(a, form, form, title="Опасный URL", url="javascript:void(0)")[0], 422)
        response = self.send(a, form, form, title="Модель кампуса", kind="project",
                             content="<strong>Черновик</strong>", url="https://example.org/project")
        self.assertEqual(response[0], 303)
        path = response[1]["Location"]
        self.assertIn("&lt;strong&gt;Черновик&lt;/strong&gt;", a.request(path)[2])
        self.assertEqual(b.request(path)[0], 404)
        oid = self.db_one("SELECT id FROM opportunity ORDER BY title LIMIT 1")
        tracker = f"/opportunities/{oid}"
        self.assertEqual(self.send(a, tracker, tracker, status="sent_manual", note="Синтетическая проверка")[0], 303)
        self.assertIn("Синтетическая проверка", self.client("student-a").request(tracker)[2])
        self.assertNotIn("Синтетическая проверка", b.request(tracker)[2])
        self.assertEqual(self.send(a, path, path + "/delete")[0], 303)
        self.assertEqual(a.request(path)[0], 404)
