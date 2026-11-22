from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from datetime import datetime, time, timedelta
from html import escape
from urllib.parse import urlencode
from zoneinfo import ZoneInfo

from dean.clock import at
from dean.db import connect
from dean.domain import timestamp

from dean.reservations import fixture_id
from dean.seed import UNIVERSITY

from .test_dean import DemoFixture


class ReservationTest(DemoFixture):
    def send(self, client, page, action, **values):
        return client.request(action, {**client.fields(page, action), **values})

    def test_last_sport_seat_cancel_waitlist_and_calendar(self):
        a, b = self.client("student-a"), self.client("student-b")
        sid = fixture_id(UNIVERSITY, "sport-volley:0")
        source, post = f"/slots/{sid}", f"/slots/{sid}/book"
        payloads = [a.fields(source, post), b.fields(source, post)]
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda pair: pair[0].request(post, {**pair[1], "ack": "1"}),
                                    zip((a,b), payloads)))
        self.assertEqual(sorted(x[0] for x in results), [303,409])
        win = 0 if results[0][0] == 303 else 1
        students = (a,b)
        owner, other = students[win], students[1-win]
        record = results[win][1]["Location"]
        self.assertEqual(owner.request(post, {**payloads[win], "ack": "1"})[1]["Location"], record)
        self.assertEqual(other.request(record)[0], 404)
        self.assertIn('data-confirmation role="status"', owner.request(record)[2])
        self.assertNotIn('data-confirmation role="status"', owner.request(record)[2])
        day = self.today + timedelta(days=1)
        self.assertIn(record, owner.request(f"/schedule?date={day}&kind=booking")[2])
        self.assertNotIn(record, other.request(f"/schedule?date={day}&kind=booking")[2])
        self.assertEqual(self.send(other, source, source+"/wait")[0], 303)
        cancel = record + "/cancel"
        fields = owner.fields(record, cancel)
        self.assertEqual(owner.request(cancel, fields)[0], 303)
        self.assertEqual(owner.request(cancel, fields)[0], 303)
        self.assertNotIn(record, owner.request(f"/schedule?date={day}&kind=booking")[2])
        self.assertIn("Место могло освободиться", other.request("/notifications?kind=activity")[2])
        self.assertEqual(self.db_one("SELECT count(*) FROM reservation WHERE status='reserved'"), 0)
        self.assertEqual(self.db_one("SELECT count(*) FROM notification WHERE event_key LIKE 'wait:%'"), 1)
        self.assertEqual(self.send(other, source, post, ack="1")[0], 303)

    def test_room_intervals_allow_touching_endpoint_and_staff_cannot_break_booking(self):
        a, b, staff = (self.client(name) for name in ("student-a", "student-b", "staff"))
        sid = fixture_id(UNIVERSITY, "room-focus:0")
        page, post = f"/slots/{sid}", f"/slots/{sid}/book"
        day = self.today + timedelta(days=1)
        self.assertEqual(self.send(a, page, post, starts=f"{day}T09:00", ends=f"{day}T11:00", ack="1")[0], 409)
        self.assertEqual(self.send(a, page, post, starts=f"{day}T10:00", ends=f"{day}T11:00", ack="1")[0], 303)
        self.assertEqual(self.send(b, page, post, starts=f"{day}T10:45", ends=f"{day}T12:00", ack="1")[0], 409)
        self.assertEqual(self.send(b, page, post, starts=f"{day}T11:00", ends=f"{day}T12:00", ack="1")[0], 303)
        self.assertIn("Занятые интервалы", a.request(page)[2])
        staff_page = f"/staff/slots/{sid}"
        self.assertEqual(a.request(staff_page)[0], 403)
        self.assertIn("Мира Яроветова", staff.request(staff_page)[2])
        self.assertEqual(self.send(staff, staff_page, staff_page, starts=f"{day}T11:00",
                                  ends=f"{day}T14:00", capacity="1")[0], 409)
        self.assertEqual(self.send(staff, staff_page, staff_page, starts=f"{day}T10:00",
                                  ends=f"{day}T14:00", capacity="1", closed="1")[0], 303)
        self.assertNotIn("Подтвердить запись", a.request(page)[2])
        self.assertEqual(self.db_one("SELECT count(*) FROM reservation WHERE status='reserved'"), 2)

    def test_sport_lesson_conflict_requires_confirmation(self):
        a = self.client("student-a")
        sport = fixture_id(UNIVERSITY, "sport-volley:0")
        page, post = f"/slots/{sport}", f"/slots/{sport}/book"
        self.assertIn("Интервал пересекается с учебной парой", a.request(page)[2])
        self.assertEqual(self.send(a, page, post)[0], 422)
        self.assertEqual(self.send(a, page, post, ack="1")[0], 303)
        self.assertIn("Открытая игра в волейбол", a.request("/bookings/mine?kind=sport")[2])

    def test_room_conflict_is_based_on_selected_interval_and_preserves_edits(self):
        student = self.client("student-b")
        sid = fixture_id(UNIVERSITY, "room-focus:0")
        page, post = f"/slots/{sid}", f"/slots/{sid}/book"
        zone = ZoneInfo("Europe/Moscow")
        with closing(connect(self.path)) as db:
            db.execute("UPDATE reservation_slot SET starts_at=?,ends_at=? WHERE id=?", (
                timestamp(datetime.combine(self.today, time(9), zone)),
                timestamp(datetime.combine(self.today, time(20), zone)), sid))
        booking = {"starts": f"{self.today}T10:45", "ends": f"{self.today}T11:45"}
        selection = page + "?" + urlencode(booking)
        with at(datetime.combine(self.today, time(8), zone)):
            rendered = student.request(selection)[2]
            self.assertIn("data-room-conflict hidden", rendered)
            self.assertIn("data-room-ack hidden", rendered)
            self.assertNotIn("Пара пересекает окно", rendered)
            fields = student.fields(selection, post)
            status, _, error = student.request(post, {**fields, "starts": f"{self.today}T09:45"})
            self.assertEqual(status, 422)
            self.assertIn('value="' + f"{self.today}T09:45" + '"', error)
            self.assertIn("Интервал пересекается с учебной парой. Общая физика.", error)
            self.assertIn('data-room-ack><input', error)
            self.assertEqual(student.request(post, fields)[0], 303)

    def test_ongoing_room_window_suggests_a_future_available_interval(self):
        a=self.client("student-a")
        sid=fixture_id(UNIVERSITY,"room-focus:0")
        page=f"/slots/{sid}"
        moment=datetime.combine(self.today+timedelta(days=1),time(12),ZoneInfo("Europe/Moscow"))
        with at(moment):
            fields=a.fields(page,page+"/book")
            self.assertTrue(fields["starts"].endswith("T12:01"))
            self.assertTrue(fields["ends"].endswith("T13:01"))
            self.assertEqual(a.request(page+"/book",{**fields,"ack":"1"})[0],303)

    def test_room_timetable_prefills_booking_and_shares_capacity_with_staff(self):
        a, b, staff = (self.client(name) for name in ("student-a", "student-b", "staff"))
        sid = fixture_id(UNIVERSITY, "room-focus:0")
        day = self.today + timedelta(days=1)
        timetable = f"/bookings?kind=room&date={day}"
        interval = {"starts": f"{day}T12:00", "ends": f"{day}T13:00"}
        slot = f"/slots/{sid}?" + urlencode(interval)
        booking = f"/slots/{sid}/book"
        status, _, html = a.request(timetable)
        self.assertEqual(status, 200)
        self.assertEqual(html.count('class="dean-room-table"'), 1)
        self.assertIn(escape(slot, quote=True), html)
        mobile = html.split('class="dean-room-mobile">', 1)[1]
        self.assertIn(f'<li data-state="open"><a href="{escape(slot, quote=True)}"', mobile)
        fields = a.fields(slot, booking)
        self.assertEqual({key: fields[key] for key in interval}, interval)
        result = a.request(booking, {**fields, "ack": "1"})
        self.assertEqual(result[0], 303)
        self.assertIn(f'data-state="own"><a href="{result[1]["Location"]}"', a.request(timetable)[2])
        other = b.request(timetable)[2]
        self.assertIn('data-state="busy" aria-label="Занято">Занято', other)
        self.assertNotIn(result[1]["Location"], other)
        self.assertIn("Мира Яроветова", staff.request(f"/staff/slots/{sid}")[2])
