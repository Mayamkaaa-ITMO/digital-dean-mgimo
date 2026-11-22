from contextlib import closing
from unittest.mock import patch

from dean import public_schedule, public_sync
from dean.db import connect
from dean.domain import DomainError
from tests.test_dean import DemoFixture

TUSUR = ("tusur-timetable", "fvs/516-1")
POLYTECH = ("spbpu-ruz", "125/45492")
SUSU = ("susu-timetable", "ke-102")
NSPU = ("nspu-reference", "3333")
GUAP = ("guap-reference", "7906")
MONDAY = "2026-11-16"


class PublicUniversitiesTest(DemoFixture):
    def choose(self, client, selection):
        page = "/schedule/context"
        data = {**client.fields(page, page), "selection": "|".join(selection)}
        status, headers, _ = client.request(page, data)
        self.assertEqual((status, headers["Location"]), (303, "/schedule"))

    def publish(self, selection):
        week = public_schedule.week_scope(*selection, MONDAY)
        with closing(connect(self.path)) as db:
            report = public_sync.publish(db, public_sync.stage_sample(db, week))
        return week, report

    def test_five_contexts_are_selectable_with_their_own_pages(self):
        student = self.client("student-a")
        body = student.request("/schedule/context")[2]
        for selection in (TUSUR, POLYTECH, SUSU, NSPU, GUAP):
            self.assertIn("|".join(selection), body)
            self.choose(student, selection)
            status, _, schedule = student.request("/schedule?date=" + MONDAY)
            self.assertEqual(status, 200)
            source = public_schedule.SOURCES[selection[0]]
            self.assertIn(source["name"], schedule)
            self.assertIn(source["groups"][selection[1]]["label"], schedule)
            self.assertIn("Официальный сайт", student.request("/student-services")[2])
        self.assertIn("Учебный стенд · вымышленные данные", student.request("/grades")[2])

    def test_dated_snapshots_keep_the_campus_clock_and_mark_unknown_days(self):
        student = self.client("student-a")
        week, report = self.publish(POLYTECH)
        self.assertEqual(report["added"], 11)
        events = public_schedule.sample_events(week)
        for event in events:
            self.assertTrue(event.starts_at.endswith("+00:00"))

        self.choose(student, POLYTECH)
        status, _, schedule = student.request("/schedule?view=day&date=" + MONDAY)
        self.assertEqual(status, 200)
        self.assertIn("16:00–17:40", schedule)
        self.assertIn("Методы принятия решений", schedule)
        self.assertIn("Чётная неделя", schedule)
        self.assertIn("проверенная выборка", schedule)
        self.assertIn("Данные на этот день не получены.",
                      student.request("/schedule?view=day&date=2026-11-19")[2])
        room_path = "/schedule/room/research-a108"
        self.assertEqual(student.request(room_path)[0], 200)
        self.assertIn("Научно-исследовательский корпус", student.request(room_path)[2])

        week, report = self.publish(SUSU)
        self.assertEqual(report["added"], 23)
        lesson = next(event for event in public_schedule.sample_events(week)
                      if event.subject == "Информатика" and event.starts_at.startswith(MONDAY + "T03:00"))
        self.choose(student, SUSU)
        status, _, schedule = student.request("/schedule?view=day&date=" + MONDAY)
        self.assertEqual(status, 200)
        self.assertIn("08:00–09:35", schedule)
        self.assertIn("2026-11-16T03:00:00+00:00", schedule)
        self.assertIn("Asia/Yekaterinburg", schedule)
        self.assertIn("Дата обновления на сайте вуза 2026-11-17", schedule)
        self.assertEqual(student.request(room_path)[0], 404)
        path = "/schedule/public/" + public_schedule.lesson_id(week, lesson.external_id)
        self.assertIn("/schedule/room/susu-434-3b", student.request(path)[2])
        self.assertIn("Адрес аудитории в использованной сетке не указан.",
                      student.request("/schedule/room/susu-434-3b")[2])

    def test_reference_grids_do_not_create_dated_lessons(self):
        student = self.client("student-a")
        for selection, phrase, linked in (
            (NSPU, "Основы медицинских знаний", "/schedule/room/room313"),
            (GUAP, "Аналитическая геометрия", "/schedule/room/guap-gastello"),
        ):
            self.choose(student, selection)
            _, _, schedule = student.request("/schedule?date=" + MONDAY)
            self.assertIn("Справочная сетка группы", schedule)
            self.assertIn("Привязка к датам пока не подтверждена.", schedule)
            self.assertIn(phrase, schedule)
            self.assertNotIn("/schedule/public/", schedule)
            self.assertEqual(student.request(linked)[0], 200)
            with self.assertRaises(DomainError):
                public_schedule.week_scope(*selection, MONDAY)
        self.assertIn("Дата сборки на сайте 2026-08-31.", schedule)
        self.assertEqual(self.db_one("SELECT count(*) FROM public_lesson"), 0)
        self.assertEqual(student.request("/schedule/room/room313")[0], 404)

    def test_public_note_survives_reimport_and_campus_switch(self):
        week, _ = self.publish(POLYTECH)
        a = self.client("student-a")
        b = self.client("student-b")
        event = public_schedule.sample_events(week)[0]
        path = "/schedule/public/" + public_schedule.lesson_id(week, event.external_id)
        self.choose(a, POLYTECH)
        self.choose(b, POLYTECH)
        note_path = path + "/note"
        data = {**a.fields(path, note_path), "body": "Заметка к паре СПбПУ"}
        self.assertEqual(a.request(note_path, data)[0], 303)
        self.assertNotIn("Заметка к паре СПбПУ", b.request(path)[2])
        self.assertEqual(self.publish(POLYTECH)[1]["added"], 0)
        self.choose(a, SUSU)
        self.assertEqual(a.request(path)[0], 404)
        self.choose(a, POLYTECH)
        self.assertIn("Заметка к паре СПбПУ", self.read_after_restart(a, path))

    def test_twelve_dated_scopes_reimport_without_network_or_duplicates(self):
        with closing(connect(self.path)) as db, patch(
            "dean.public_transport.read_week", side_effect=AssertionError("unexpected network")
        ):
            count = 0
            scopes = 0
            for source_id, source in public_schedule.SOURCES.items():
                for group_id, group in source["groups"].items():
                    for day in group["weeks"]:
                        week = public_schedule.week_scope(source_id, group_id, day)
                        scopes += 1
                        count += public_sync.publish(db, public_sync.stage_sample(db, week))["added"]
                        repeated = public_sync.publish(db, public_sync.stage_sample(db, week))
                        self.assertFalse(any(repeated.values()))
            self.assertEqual((count, scopes), (219, 12))
            self.assertEqual(db.execute("SELECT count(*) FROM public_lesson").fetchone()[0], count)
            self.assertFalse(list(db.execute("PRAGMA foreign_key_check")))

    def test_new_source_refuses_live_import_before_transport(self):
        week = public_schedule.week_scope(*POLYTECH, MONDAY)
        with closing(connect(self.path)) as db, patch.dict(
            public_schedule.SOURCES[POLYTECH[0]], live_enabled=True
        ):
            with self.assertRaises(DomainError) as failure:
                public_sync.stage_live(db, week, read=lambda *_: self.fail("Unexpected network call"))
        self.assertEqual(failure.exception.status, 409)
