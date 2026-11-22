from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from uuid import uuid4

from dean.clock import at
from dean.fixtures import demo_id
from dean.reservations import fixture_id
from dean.seed import UNIVERSITY, seed_id

from .test_dean import DemoFixture


class CampusProcessTest(DemoFixture):
    def post(self, client, page, action, **values):
        return client.request(action, {**client.fields(page, action), **values})

    def test_single_equipment_unit_calendar_and_issue_history(self):
        a, b, staff = (self.client(login) for login in ("student-a", "student-b", "staff"))
        uid = demo_id(UNIVERSITY, "equipment", "Н-01")
        url = "/equipment/" + uid
        action = url + "/reserve"
        day = self.today + timedelta(days=1)
        values = dict(starts=f"{day}T16:00", ends=f"{day}T18:00", purpose="Учебный проект", software="Python")
        fields = [{**client.fields(url, action), **values} for client in (a, b)]
        with ThreadPoolExecutor(max_workers=2) as pool:
            pending = [pool.submit(c.request, action, f) for c, f in zip((a, b), fields)]
            responses = [p.result() for p in pending]
        self.assertEqual(sorted(r[0] for r in responses), [303, 409])
        winner, other = (a, b) if responses[0][0] == 303 else (b, a)
        path = next(r[1]["Location"] for r in responses if r[0] == 303)
        self.assertEqual(other.request(path)[0], 404)
        self.assertIn(path, winner.request(f"/schedule?date={day}&kind=booking")[2])
        maintenance = f"/staff/equipment/{uid}/maintenance/enable"
        self.assertEqual(self.post(staff, "/staff/equipment", maintenance, reason="Проверка")[0], 409)
        begin = datetime.fromisoformat(self.db_one("SELECT starts_at FROM equipment_loan"))
        staff_path = "/staff" + path
        with at(begin):
            issue = staff_path + "/issue"
            issued = staff.fields(staff_path, issue)
            self.assertEqual(staff.request(issue, issued)[0], 303)
            self.assertEqual(staff.request(issue, issued)[0], 303)
            self.assertIn("Единиц техники на руках 1", staff.request("/staff")[2])
            self.assertEqual(self.post(staff, staff_path, staff_path + "/return")[0], 303)
        self.assertIn("Возвращено", self.read_after_restart(winner, path))
        self.assertEqual(self.db_one("SELECT COUNT(*) FROM equipment_history WHERE loan_id IS NOT NULL"), 3)
        self.assertEqual(self.post(staff, "/staff/equipment", maintenance, reason="Проверка")[0], 303)
        self.assertIn("Единица на обслуживании", a.request(url)[2])

    def test_library_return_queue_expiration_and_pickup(self):
        a, b, staff = (self.client(login) for login in ("student-a", "student-b", "staff"))
        bid = self.db_one("SELECT id FROM library_book WHERE title LIKE 'Практикум%'")
        copy = self.db_one("SELECT id FROM library_copy WHERE book_id=?", (bid,))
        action = "/staff/library/issue"
        issued = self.post(staff, "/staff/library", action, student=seed_id("student-a"), copy=copy)
        self.assertEqual(issued[0], 303)
        loan = issued[1]["Location"]
        own = loan.removeprefix("/staff")
        reserve = f"/library/books/{bid}/reserve"
        catalog = a.request("/library")[2]
        self.assertIn("У тебя на руках · открыть выдачу", catalog)
        self.assertIn(f'href="{own}"', catalog)
        self.assertNotIn(f'action="{reserve}"', catalog)
        queue = self.post(b, "/library", reserve)[1]["Location"]
        reserved = b.request("/library")[2]
        self.assertIn(f'href="{queue}"', reserved)
        self.assertNotIn(f'action="{reserve}"', reserved)
        self.assertIn("В очереди", b.request(queue)[2])
        self.assertEqual(a.request(queue)[0], 404)
        self.assertEqual(self.post(a, own, own + "/renew")[0], 409)
        self.assertEqual(self.post(staff, loan, loan + "/return")[0], 303)
        self.assertIn("Можно получить", b.request(queue)[2])
        self.assertEqual(self.post(staff, "/staff/library", action, student=seed_id("student-a"), copy=copy)[0], 409)
        next_hold = self.post(a, "/library", reserve)[1]["Location"]
        self.assertIn("В очереди", a.request(next_hold)[2])
        expires = datetime.fromisoformat(self.db_one("SELECT expires_at FROM library_hold WHERE state='offered'"))
        with at(expires):
            self.assertIn("Можно получить", a.request(next_hold)[2])
            self.assertIn("Срок резерва истёк", b.request(queue)[2])
            hid = next_hold.rsplit("/", 1)[-1]
            pick = "/staff/library/issue/" + hid
            payload = staff.fields("/staff/library", pick)
            self.assertEqual(staff.request(pick, payload)[0], 303)
            self.assertEqual(staff.request(pick, payload)[0], 303)
        self.assertIn("Получено", a.request(next_hold)[2])
        self.assertEqual(self.db_one("SELECT COUNT(*) FROM library_loan WHERE returned_at IS NULL"), 1)

    def test_sport_consent_revocation_and_confirmed_progress(self):
        a, b, staff = (self.client(login) for login in ("student-a", "student-b", "staff"))
        sid = fixture_id(UNIVERSITY, "sport-volley:0")
        url = "/slots/" + sid
        booking = self.post(a, url, url + "/book", ack="1")[1]["Location"]
        action = url + "/auto-wait"
        data = {**b.fields(url, action), "consent": "1"}
        self.assertEqual(b.request(action, {**data, "consent": ""})[0], 422)
        self.assertEqual(b.request(action, data)[0], 303)
        self.assertEqual(self.post(b, url, url + "/withdraw-auto")[0], 303)
        self.assertEqual(self.post(a, booking, booking + "/cancel")[0], 303)
        self.assertEqual(self.db_one("SELECT COUNT(*) FROM reservation WHERE status='reserved'"), 0)
        booking = self.post(a, url, url + "/book", ack="1")[1]["Location"]
        self.assertEqual(self.post(b, url, action, consent="1")[0], 303)
        self.assertEqual(self.post(a, booking, booking + "/cancel")[0], 303)
        self.assertIn("Место подтверждено автоматически", b.request("/notifications")[2])
        rid = self.db_one("SELECT id FROM reservation WHERE status='reserved'")
        ends = datetime.fromisoformat(self.db_one("SELECT ends_at FROM reservation WHERE id=?", (rid,)))
        manage = "/staff" + url
        mark = f"{manage}/attendance/{rid}"
        with at(ends):
            fields = staff.fields(manage, mark)
            self.assertEqual(staff.request(mark, fields)[0], 303)
            self.assertEqual(staff.request(mark, {**fields, "request_id": uuid4().hex})[0], 303)
        self.assertIn("Посещений 1", b.request("/bookings/mine?kind=sport")[2])
        self.assertIn("Посещений 0", a.request("/bookings/mine?kind=sport")[2])

    def test_room_link_feeds_repair_template(self):
        a = self.client("student-a")
        event = seed_id("a1")
        room = self.db_one("SELECT room_id FROM schedule_event WHERE id=?", (event,))
        service = "/services/room-repair"
        self.assertIn(service + "?room=" + room, a.request("/schedule/" + event)[2])
        card = a.request(f"/bookables/{room}")[2]
        self.assertIn(service + "?room=" + room, card)
        sent = self.post(a, service, service, room=room, issue="repair", details="Не работает демо-проектор.")
        self.assertEqual(sent[0], 303)
        self.assertIn("А-214", a.request(sent[1]["Location"])[2])
