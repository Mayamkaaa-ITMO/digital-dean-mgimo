from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from datetime import datetime, time, timedelta
from unittest.mock import patch
from urllib.parse import urlencode
from zoneinfo import ZoneInfo
from wsgiref.validate import validator

from dean import activity_extensions, clock
from dean.db import connect
from dean.web import create_app

from .test_dean import DemoFixture

ZONE = ZoneInfo("Europe/Moscow")
FORM = "/staff/manage/resources/new"


class EventExtensionsTest(DemoFixture):
    def setUp(self):
        super().setUp()
        flags = patch.dict("os.environ", {"DEAN_EXT_EVENTS": "1"})
        flags.start()
        self.addCleanup(flags.stop)
        self.a, self.b, self.staff = (self.client(name) for name in ("student-a", "student-b", "staff"))
        self.start = datetime.combine(self.today + timedelta(days=1), time(19), ZONE)
        self.title = "Открытый практикум по дизайну"

    def local(self, stamp):
        return stamp.strftime("%Y-%m-%dT%H:%M")

    def event(self, *, title="", room="a1-1012", capacity="1", start=None):
        start = start or self.start
        group = self.db_one("SELECT group_id FROM account WHERE username='student-a'")
        fields = {**self.staff.fields(FORM + "?kind=activity", FORM), "group": group, "kind": "activity",
                  "title": title or self.title, "category": "Проекты", "host": "Лада Синтетова",
                  "rules": "Принеси эскиз.", "place": "Учебный корпус",
                  "map_room": room, "starts": self.local(start), "ends": self.local(start + timedelta(hours=1)),
                  "capacity": capacity}
        status, headers, body = self.staff.request(FORM, fields)
        self.assertEqual(status, 303, body)
        self.assertEqual(self.staff.request(FORM, fields)[1]["Location"], headers["Location"])
        detail = headers["Location"]
        bid = detail.rsplit("/", 1)[1]
        sid = self.db_one("SELECT id FROM reservation_slot WHERE bookable_id=?", (bid,))
        return detail, "/bookables/" + bid, "/slots/" + sid

    def change(self, detail, action, **values):
        route = detail + "/" + action
        return self.staff.request(route, {**self.staff.fields(detail, route), **values})

    def assert_status(self, expected, response):
        self.assertEqual(response[0], expected, response[2])

    def book(self, student, window):
        route = window + "/book"
        return student.request(route, {**student.fields(window, route), "ack": "1"})

    def make_group_peer(self):
        with closing(connect(self.path)) as db:
            db.execute("""
                UPDATE account SET group_id=(SELECT group_id FROM account WHERE username='student-a')
                WHERE username='student-b'
            """)

    def test_staff_publish_search_map_capacity_race_and_cancel(self):
        room = next(key for key, name in activity_extensions.rooms().items() if name.endswith(" · 1012"))
        detail, offer, window = self.event(room=room)
        query = "/activities?" + urlencode({"q": "синтетова практикум", "period": "week", "free": "1"})
        self.assertNotIn(self.title, self.a.request(query)[2])
        self.assert_status(404, self.a.request(offer))
        self.assert_status(403, self.a.request(detail))
        self.assert_status(303, self.change(detail, "publish"))
        self.assertIn(self.title, self.a.request(query)[2])
        self.assertIn("Свободно 1 место", self.a.request(query)[2])
        card = self.a.request(offer)[2]
        self.assertIn("Лада Синтетова", card)
        self.assertIn("Маршрут до аудитории", card)
        self.assert_status(200, self.a.request(f"/campus-map?to={room}"))
        self.assert_status(404, self.b.request(window))
        self.make_group_peer()
        payloads = [{**student.fields(window, window + "/book"), "ack": "1"} for student in (self.a, self.b)]
        with ThreadPoolExecutor(max_workers=2) as pool:
            result = list(pool.map(lambda pair: pair[0].request(window + "/book", pair[1]),
                                   zip((self.a, self.b), payloads)))
        self.assertEqual(sorted(item[0] for item in result), [303, 409])
        index = 0 if result[0][0] == 303 else 1
        booking = result[index][1]["Location"]
        owner, other = (self.a, self.b) if index == 0 else (self.b, self.a)
        self.assertEqual(owner.request(window + "/book", payloads[index])[1]["Location"], booking)
        self.assertNotIn(self.title, owner.request(query)[2])
        self.assert_status(404, other.request(booking + "/calendar.ics"))
        self.assert_status(200, owner.request(booking + "/calendar.ics"))
        self.assertIn(self.title, owner.request(booking + "/calendar.ics")[2])
        date = self.start.date()
        self.assertIn(booking, owner.request(f"/schedule?date={date}&view=day")[2])
        self.assertNotIn(booking, other.request(f"/schedule?date={date}&view=day")[2])
        self.assertIn("Подтверждённые участники", self.staff.request("/staff" + window)[2])
        self.assert_status(403, other.request("/staff" + window))
        path = booking + "/cancel"
        fields = owner.fields(booking, path)
        self.assert_status(303, owner.request(path, fields))
        self.assert_status(303, owner.request(path, fields))
        self.assert_status(303, self.book(other, window))
        self.assertEqual(self.db_one("SELECT count(*) FROM reservation WHERE status='reserved'"), 1)

    def test_move_reminder_qr_portfolio_and_restart(self):
        detail, _, window = self.event(room="")
        self.assert_status(303, self.change(detail, "publish"))
        booking = self.book(self.a, window)[1]["Location"]
        path = booking + "/reminder"
        prefs = self.a.fields(booking, path)
        self.assert_status(303, self.a.request(path, prefs))
        self.assert_status(303, self.a.request(path, prefs))
        self.assert_status(404, self.b.request(path, {**prefs, "csrf": self.b.fields("/", "/assistant")["csrf"]}))
        with clock.at(self.start - timedelta(hours=2)):
            self.assertIn("Скоро мероприятие", self.a.request("/notifications")[2])
            self.assertNotIn("Скоро мероприятие", self.b.request("/notifications")[2])
            self.assertEqual(self.db_one("SELECT count(*) FROM notification WHERE event_key LIKE 'activity-reminder:%'"), 1)
            moved = self.start + timedelta(days=1)
            self.assert_status(303, self.change(detail, "move", starts=self.local(moved),
                                               ends=self.local(moved + timedelta(hours=1)), capacity="2",
                                               title=self.title + " и проверка"))
            self.assertNotIn("Скоро мероприятие", self.a.request("/notifications")[2])
        self.assertIn("Мероприятие перенесено", self.a.request("/notifications")[2])
        self.assertIn("Правка", self.staff.request(detail)[2])
        self.a.app = validator(create_app(self.path))
        self.assertIn("и проверка", self.a.request(booking)[2])
        self.assertIn("Напоминание за 24 часа включено", self.a.request(booking)[2])
        self.assertNotIn(booking, self.a.request(f"/schedule?date={self.start.date()}&view=day")[2])
        self.assertIn(booking, self.a.request(f"/schedule?date={moved.date()}&view=day")[2])
        ticket = booking + "/ticket"
        issued = self.a.request(ticket, self.a.fields(booking, ticket))
        self.assert_status(200, issued)
        self.assertIn('alt="QR-код билета"', issued[2])
        code = issued[2].split('name="ticket" value="', 1)[1].split('"', 1)[0]
        with clock.at(moved - timedelta(minutes=10)):
            check = "/staff" + window + "/tickets"
            template = self.staff.fields(check, check)
            self.assert_status(404, self.staff.request(check, {**template, "code": "DEAN1-invalid"}))
            review = self.staff.request(check, {**template, "code": code})[1]["Location"]
            form = self.staff.fields(review, review)
            self.assert_status(303, self.staff.request(review, form))
            self.assert_status(303, self.staff.request(review, form))
            self.assertIn("Посещение уже отмечено", self.staff.request(review)[2])
            outcome = self.a.request(booking)[2]
            self.assertIn("Посещение подтверждено", outcome)
            self.assertIn("Открыть достижение в портфолио", outcome)
            eid = self.db_one("SELECT entry_id FROM portfolio_verification WHERE reservation_id=?", (booking.rsplit("/", 1)[1],))
            self.assert_status(404, self.b.request("/portfolio/" + eid))
            self.assert_status(409, self.change(detail, "retire"))
        self.assertEqual(self.db_one("SELECT count(*) FROM reservation_attendance"), 1)

    def test_retire_releases_bookings_and_old_reminders_and_flag_off(self):
        self.make_group_peer()
        detail, offer, window = self.event(room="", capacity="2")
        self.assert_status(303, self.change(detail, "publish"))
        bookings = [self.book(student, window)[1]["Location"] for student in (self.a, self.b)]
        self.assert_status(409, self.change(detail, "move", starts=self.local(self.start),
                                           ends=self.local(self.start + timedelta(hours=1)), capacity="1"))
        path = bookings[0] + "/reminder"
        with clock.at(self.start - timedelta(hours=1)):
            self.assert_status(303, self.a.request(path, self.a.fields(bookings[0], path)))
            self.assertIn("Скоро мероприятие", self.a.request("/notifications")[2])
            retire = detail + "/retire"
            payload = self.staff.fields(detail, retire)
            self.assert_status(303, self.staff.request(retire, payload))
            self.assert_status(303, self.staff.request(retire, payload))
            self.assert_status(404, self.a.request(offer))
            for student, booking in zip((self.a, self.b), bookings):
                self.assertIn("Отменена", student.request(booking)[2])
                self.assertIn("Мероприятие отменено", student.request("/notifications")[2])
                self.assertNotIn("Скоро мероприятие", student.request("/notifications")[2])
            self.assertIn("Отменено регистраций: 2", self.staff.request(detail)[2])
        with patch.dict("os.environ", {"DEAN_EXT_EVENTS": "0"}):
            self.assertNotIn('name="q"', self.a.request("/activities")[2])
            self.assert_status(404, self.a.request(bookings[0] + "/calendar.ics"))
