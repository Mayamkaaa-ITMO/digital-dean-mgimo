from datetime import datetime, timedelta
from unittest.mock import patch

from dean import access_paths, auth, clock
from dean.reservations import fixture_id
from dean.seed import UNIVERSITY, seed_id
from dean.web import create_app
from tests.test_assisted_dean import Model, PHOTO, PIXEL, upload
from tests.test_dean import DemoFixture, Forms


class SessionRecoveryTest(DemoFixture):
    def sign_in_for(self, target, account):
        browser = self.client()
        entry = browser.request(target)
        self.assertEqual(entry[0], 303)
        fields = browser.fields(entry[1]["Location"], "/login")
        status, headers, _ = browser.request("/login", {**fields, "username": account,
                                                       "password": self.passwords[account]})
        self.assertEqual(status, 303)
        return browser, headers["Location"]

    def test_login_returns_to_the_requested_page_only_when_the_role_matches(self):
        staff, destination = self.sign_in_for("/bookings", "staff")
        self.assertEqual(destination, "/staff")
        self.assertEqual(staff.request(destination)[0], 200)
        self.assertEqual(staff.request(access_paths.login_url("/assistant"))[1]["Location"], "/assistant")
        _, destination = self.sign_in_for("/staff/bookings", "student-a")
        self.assertEqual(destination, "/")
        student, destination = self.sign_in_for("/bookings", "student-b")
        self.assertEqual(destination, "/bookings")
        self.assertEqual(student.request(destination)[0], 200)

    def test_invalid_booking_interval_can_be_corrected_on_the_same_form(self):
        student = self.client("student-a")
        sid = fixture_id(UNIVERSITY, "room-focus:0")
        start = datetime.fromisoformat(self.db_one("SELECT starts_at FROM reservation_slot WHERE id=?", (sid,)))
        slot = "/slots/" + sid
        action = slot + "/book"
        with clock.at(start - timedelta(days=1)):
            fields = student.fields(slot, action)
            invalid = {"starts": fields["ends"], "ends": fields["starts"]}
            status, _, page = student.request(action, {**fields, **invalid})
            self.assertEqual(status, 422)
            self.assertIn("Бронь помещения должна длиться больше нуля", page)
            updated = Forms(page).forms[action]
            self.assertEqual((updated["starts"], updated["ends"]), (invalid["starts"], invalid["ends"]))
            self.assertEqual(updated["csrf"], fields["csrf"])
            status, headers, _ = student.request(action, {**updated, "starts": fields["starts"],
                                                          "ends": fields["ends"], "ack": "1"})
            self.assertEqual(status, 303)
            reservation = headers["Location"].removeprefix("/bookings/")
            self.assertEqual(self.db_one("SELECT student_id FROM reservation WHERE id=?", (reservation,)),
                             seed_id("student-a"))

    def test_old_forms_cannot_write_to_another_account_and_expired_forms_can_be_reopened(self):
        engine = Model({"events": [{"day": 1, "start": "08:50", "end": "10:25", "subject": "Занятие на фото",
                                    "room": "А-301", "kind": "семинар", "uncertain": False}]})
        self.app = create_app(self.path, ai_engine=engine)
        student, other = self.client("student-a"), self.client("student-b")
        page = "/tasks/new"
        fields = student.fields(page, page)
        data = {**fields, "title": "Задача из старого сеанса", "priority": "normal"}
        status, _, error = other.request(page, data, HTTP_REFERER="https://outside.test/grades")
        self.assertEqual(status, 403)
        self.assertIn("Эта отправка не выполнена", error)
        self.assertIn('href="/tasks/new"', error)
        self.assertNotIn('href="/grades" class="dean-secondary-action"', error)
        self.assertEqual(self.db_one("SELECT count(*) FROM task WHERE title=?", (data["title"],)), 0)

        with self.subTest("multipart form after switching accounts"):
            photo = student.fields(PHOTO, PHOTO)
            result = upload(other, PHOTO, photo, PIXEL)
            self.assertEqual(result[0], 403)
            self.assertIn(f'href="{PHOTO}"', result[2])
            self.assertIn("Эта отправка не выполнена", result[2])
            self.assertFalse(engine.calls)
            self.assertEqual(upload(other, PHOTO, other.fields(PHOTO, PHOTO), PIXEL)[0], 303)
            self.assertEqual(len(engine.calls), 1)

        with self.subTest("idle session offers a safe return through login"):
            expires = auth.real_now() + auth.SESSION_IDLE + timedelta(minutes=1)
            with patch.object(auth, "real_now", return_value=expires):
                status, _, outside = student.request(page, data)
            self.assertEqual(status, 403)
            self.assertIn("Сеанс завершён", outside)
            self.assertIn(f'href="{access_paths.login_url(page)}"', outside)
            self.assertEqual(self.db_one("SELECT count(*) FROM task WHERE title=?", (data["title"],)), 0)
