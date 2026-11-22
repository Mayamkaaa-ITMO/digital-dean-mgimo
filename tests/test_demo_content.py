from contextlib import closing
from datetime import datetime, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from uuid import uuid4

from dean import activity_extensions, applications, reservations, services
from dean.clock import at as freeze_clock
from dean.db import connect, migrate
from dean.fixtures import demo_id
from dean.seed import UNIVERSITY, ZONE, at, seed, seed_id
from dean.web import create_app
from scripts.campus_lab_setup import setup
from tests.test_dean import Client


DEMO_TIME = datetime(2026, 11, 22, 10, tzinfo=ZONE)
ACCOUNTS = {"student-a": "student-a-demo-password", "student-b": "student-b-demo-password",
            "staff": "staff-demo-password"}


class DemoContentTest(TestCase):
    def test_b_demo_can_open_own_pdf_and_announcement(self):
        with TemporaryDirectory() as temp, freeze_clock(DEMO_TIME):
            path = Path(temp) / "dean.sqlite3"
            with closing(connect(path)) as db:
                migrate(db)
                seed(db, path, DEMO_TIME.date(), ACCOUNTS)
                setup(db)
                setup(db)
                doc, = db.execute("""
                    SELECT d.id FROM document d JOIN application a ON a.id=d.application_id
                    WHERE a.student_id=? AND a.status='ready'
                """, (seed_id("student-b"),)).fetchall()
                announcement = seed_id("showcase:energy:announcement")
                count = db.execute("SELECT count(*) FROM notification WHERE target_id=? AND student_id=?",
                                   (announcement, seed_id("student-b"))).fetchone()[0]
                self.assertEqual(count, 1)
            app = create_app(path)
            a, b, staff = (Client(app) for _ in range(3))
            for client, login in ((a, "student-a"), (b, "student-b"), (staff, "staff")):
                self.assertEqual(client.login(login, ACCOUNTS[login])[0], 303)
            pdf = f'/documents/{doc["id"]}?download=1'
            status, headers, data = b.request(pdf)
            self.assertEqual(status, 200)
            self.assertEqual(headers["Content-Type"], "application/pdf")
            self.assertTrue(data.startswith(b"%PDF-"))
            self.assertEqual(a.request(pdf)[0], 404)
            self.assertEqual(staff.request(pdf)[0], 404)
            self.assertIn("Консультация по лабораторным работам", b.request("/notifications?kind=announcement")[2])
            self.assertEqual(b.request(f"/announcements/{announcement}")[0], 200)
            self.assertEqual(a.request(f"/announcements/{announcement}")[0], 404)
            self.assertEqual(staff.request(f"/announcements/{announcement}")[0], 404)

    def test_upgrade_preserves_edits_and_populates_connected_roles(self):
        with TemporaryDirectory() as temp, freeze_clock(DEMO_TIME):
            path = Path(temp) / "dean.sqlite3"
            with closing(connect(path)) as db:
                migrate(db)
                today = DEMO_TIME.date()
                seed(db, path, today, ACCOUNTS)
                users = {row["username"]: dict(row, timezone=ZONE.key) for row in db.execute("SELECT * FROM account")}
                student, other, staff = (users[login] for login in ("student-a", "student-b", "staff"))
                legacy = seed_id("a1")
                services.move_lesson(db, staff, legacy, 1, f"{today}T10:30", f"{today}T12:00",
                                     "Аудитория после ручного переноса", uuid4().hex)
                existing = applications.create_application(db, student, "data-question",
                                                            {"details": "Моя настоящая проверочная заявка."}, uuid4().hex)
                original = dict(applications.applications(db, student, existing))

                setup(db)
                tables = ("schedule_event", "reservation_slot", "reservation", "application", "announcement",
                          "task", "notification", "note")

                def counts():
                    return [db.execute(f"SELECT count(*) FROM {table}").fetchone()[0] for table in tables]

                snapshot = counts()
                setup(db)
                self.assertEqual(counts(), snapshot)
                self.assertEqual(dict(applications.applications(db, student, existing)), original)
                self.assertEqual(tuple(db.execute("SELECT room,version FROM schedule_event WHERE id=?", (legacy,)).fetchone()),
                                 ("Аудитория после ручного переноса", 2))
                for ref, key in (("a2", "b2-2018a"), ("b1", "b1-1011"), ("b2", "b2-2011"), ("b3", "b1-1010")):
                    mapped = db.execute("SELECT room FROM schedule_event WHERE id=?", (seed_id(ref),)).fetchone()[0]
                    self.assertIn("room=" + key, activity_extensions.lesson_map_url(mapped, seed_id(ref)))

                for owner in (student, other):
                    dense = db.execute("""SELECT count(DISTINCT substr(starts_at,1,10)) FROM schedule_event
                        WHERE group_id=? AND starts_at>=? AND starts_at<?""",
                                       (owner["group_id"], at(today - timedelta(days=21), 0),
                                        at(today + timedelta(days=15), 0))).fetchone()[0]
                    self.assertGreaterEqual(dense, 23)
                requests = {row["id"]: row for row in applications.applications(db, student)}
                self.assertTrue({"draft", "submitted", "review", "needs_changes", "completed", "ready"}
                                <= {row["status"] for row in requests.values()})
                visible = {row["id"] for row in applications.applications(db, staff)}
                self.assertEqual(visible, {row["id"] for row in requests.values() if row["status"] != "draft"})
                self.assertTrue({"announcement", "task", "schedule", "application", "booking"}
                                <= {row["kind"] for row in services.notifications(db, student)})

                venue = demo_id(UNIVERSITY, "bookable", "showcase-coworking")
                slot = next(row for row in reservations.slots(db, student, venue)
                            if row["starts_at"] == at(today + timedelta(days=1), 9))
                people = reservations.participants(db, staff, slot["id"])
                self.assertEqual(len(people), 1)
                self.assertEqual(people[0]["full_name"], other["full_name"])
                self.assertEqual(db.execute("PRAGMA quick_check").fetchone()[0], "ok")

            web = create_app(path)
            browser, energy_student, worker = Client(web), Client(web), Client(web)
            self.assertEqual(browser.login("student-a", ACCOUNTS["student-a"])[0], 303)
            self.assertEqual(energy_student.login("student-b", ACCOUNTS["student-b"])[0], 303)
            self.assertEqual(worker.login("staff", ACCOUNTS["staff"])[0], 303)
            physics = "/schedule/" + seed_id("b1")
            self.assertIn("to=b1-1011", energy_student.request(physics)[2])
            schedule = energy_student.request(f"/schedule?date={today.isoformat()}&view=day")[2]
            self.assertRegex(schedule, r'data-room="/campus-map\?[^"]*room=b1-1011[^"]*#campus-place"')
            self.assertIn("Главный вход → 1011",
                          energy_student.request("/campus-map?to=b1-1011&back=" + physics)[2])
            self.assertIn("Дополнительные консультации", browser.request("/notifications?kind=announcement")[2])
            self.assertIn("Лев Светолесов", worker.request(f"/staff/slots/{slot['id']}")[2])
            self.assertIn("Ремонт и бытовое обращение", worker.request("/staff")[2])
