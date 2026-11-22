import json
from contextlib import closing
from pathlib import Path
from unittest.mock import patch
from wsgiref.validate import validator

from dean import room_occupancy
from dean.db import connect
from dean.reservations import fixture_id
from dean.seed import UNIVERSITY
from dean.web import create_app
from scripts import provision

from .test_dean import Client, DemoFixture


class OccupancyDemoTest(DemoFixture):
    def test_student_can_open_booking_catalog_and_mapped_room_from_heatmap(self):
        student = self.client("student-a")
        bid = fixture_id(UNIVERSITY, "room-focus")
        with closing(connect(self.path)) as db:
            db.execute("INSERT INTO activity_room VALUES (?,?)", (bid, "b1-1012"))
        with patch.dict("os.environ", {"DEAN_EXT_ROOM_OCCUPANCY": "1"}):
            body = student.request(room_occupancy.PATH)[2]
            self.assertIn('href="/bookings?kind=room"', body)
            self.assertIn(f'href="/bookables/{bid}" class="dean-secondary-action">Время для брони', body)
            self.assertEqual(student.request(f"/bookables/{bid}")[0], 200)

    def test_generate_aggregate_forecast_map_and_restart(self):
        student, peer = (self.client(name) for name in ("student-a", "student-b"))
        path = room_occupancy.PATH
        self.assertEqual(student.request(path)[0], 404)
        with patch.dict("os.environ", {"DEAN_EXT_ROOM_OCCUPANCY": "1"}):
            api = path + "?format=json"
            self.assertEqual(json.loads(student.request(api)[2])["rooms"], [])
            payload = student.fields(path, path)
            self.assertEqual(student.request(path, payload)[0], 303)
            self.assertEqual(student.request(path, payload)[0], 303)
            self.assertEqual(peer.request(path, payload)[0], 403)
            self.assertEqual(self.db_one("SELECT count(*) FROM demo_room_flow"), 337 * 3)
            day = json.loads(student.request(api)[2])
            week = json.loads(peer.request(api + "&period=week")[2])
            self.assertEqual(day["source"], "synthetic")
            self.assertEqual(day["updated_at"], week["updated_at"])
            for today, recent in zip(day["rooms"], week["rooms"]):
                self.assertEqual(today["latest"], recent["latest"])
                self.assertLessEqual(today["latest"], today["peak"])
                self.assertLessEqual(today["peak"], today["capacity"])
                self.assertGreater(today["forecast"]["samples"], 0)
                self.assertGreaterEqual(recent["entered"], today["entered"])
                self.assertEqual(student.request(today["map_url"])[0], 200)
            self.assertIn("Тепловая карта", student.request(path)[2])
            student.app = validator(create_app(self.path))
            self.assertEqual(json.loads(student.request(api)[2]), day)
            self.assertEqual(student.request(api + "&period=unknown")[0], 422)

    def test_floor_map_and_manual_signals_stay_in_demo_mode(self):
        path = room_occupancy.PATH
        live_path = path + "/live"
        key = "b1-1012"
        with patch.dict("os.environ", {"DEAN_EXT_ROOM_OCCUPANCY": "1", "DEAN_CAMPUS_LAB": "1"}):
            student, staff = (Client(self.app) for _ in range(2))
            self.assertEqual(student.login("student-a", self.passwords["student-a"])[0], 303)
            self.assertEqual(staff.login("staff", self.passwords["staff"])[0], 303)
            initial = json.loads(student.request(live_path)[2])
            self.assertEqual(initial[key]["source_kind"], "synthetic")
            self.assertEqual(initial[key]["level"], "low")
            self.assertIsNone(initial[key]["recorded_at"])
            self.assertIn("data-room-count", student.request(path)[2])
            self.assertEqual({room["level"] for room in initial.values()}, {"low", "medium", "high"})
            self.assertEqual(staff.request(path, staff.fields(path, path))[0], 303)
            status, headers, contents = student.request(live_path)
            self.assertEqual(status, 200)
            self.assertEqual(headers["Cache-Control"], "no-store")
            baseline = json.loads(contents)
            self.assertEqual(baseline[key]["source_kind"], "synthetic")
            for room in baseline.values():
                self.assertEqual(len(room["zones"]), 9)
                self.assertEqual(sum(zone["count"] for zone in room["zones"]), room["count"])
                self.assertEqual(sum(zone["capacity"] for zone in room["zones"]), room["capacity"])
                self.assertTrue(all(0 <= zone["count"] <= zone["capacity"] for zone in room["zones"]))
            rendered = student.request(path)[2]
            self.assertIn('aria-label="Шкала загруженности"', rendered)
            self.assertEqual(rendered.count('class="lab-active-room"'), len(baseline))
            self.assertIn('data-room-free', rendered)
            self.assertIn('Учебный снимок', rendered)
            self.assertIn('Учебное распределение по залу', rendered)
            self.assertIn('<p class="back"><a href="/all-services"', rendered)
            self.assertNotIn('Сымитировать проход', rendered)
            self.assertNotIn('data-door-camera', rendered)

            direction = "enter" if baseline[key]["count"] < baseline[key]["capacity"] else "exit"
            form = staff.fields(path, path + "/door")
            self.assertEqual(staff.request(path + "/door", {**form, "room": key, "direction": direction})[0], 303)
            changed = json.loads(student.request(live_path)[2])
            expected_delta = 1 if direction == "enter" else -1
            self.assertEqual(changed[key]["count"], baseline[key]["count"] + expected_delta)
            self.assertEqual(sum(zone["count"] for zone in changed[key]["zones"]), changed[key]["count"])
            self.assertEqual(changed[key]["source_kind"], "demo_signal")
            self.assertEqual(changed["n4-423"]["source_kind"], "synthetic")
            self.assertIn("Сигнал учебного счётчика", student.request(path)[2])

    def test_product_tenants_get_separate_demo_counters(self):
        site = json.loads(Path("deploy/site.example.json").read_text())
        with closing(connect(self.path)) as db:
            tenant = provision.register_site(db, site)
            provision.register_account(db, tenant, "other-staff", "Другой сотрудник", "staff",
                                       "tenant-specific-password", ["group-01"], None)
        flags = {"DEAN_PRODUCT": "1", "DEAN_EXT_ROOM_OCCUPANCY": "1", "DEAN_CAMPUS_LAB": "1"}
        path = room_occupancy.PATH
        key = "b1-1012"
        with patch.dict("os.environ", flags):
            staff, student = (Client(self.app) for _ in range(2))
            self.assertEqual(staff.login("other-staff", "tenant-specific-password")[0], 303)
            self.assertEqual(student.login("student-a", self.passwords["student-a"])[0], 303)
            self.assertIn('<p class="back"><a href="/campus-lab"', staff.request(path)[2])
            self.assertEqual(staff.request("/campus-lab")[0], 200)
            with patch.dict("os.environ", {"DEAN_CAMPUS_LAB": "0"}):
                standalone = Client(self.app)
                self.assertEqual(standalone.login("other-staff", "tenant-specific-password")[0], 303)
                self.assertIn('<p class="back"><a href="/staff"', standalone.request(path)[2])
                self.assertEqual(standalone.request("/staff")[0], 200)
            data = staff.fields(path, path + "/door")
            event = {**data, "room": key, "direction": "enter"}
            self.assertEqual(staff.request(path + "/door", event)[0], 303)
            self.assertEqual(staff.request(path + "/door", event)[0], 303)
            initial = room_occupancy.DEMO_COUNTS[key]
            self.assertEqual(json.loads(staff.request(path + "/live")[2])[key]["count"], initial + 1)
            self.assertEqual(json.loads(student.request(path + "/live")[2])[key]["count"], initial)
            self.assertEqual(self.db_one("SELECT university_id FROM lab_door"), tenant)
