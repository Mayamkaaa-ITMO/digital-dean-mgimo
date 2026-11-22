from contextlib import closing
from datetime import datetime, timedelta
from re import search
from unittest.mock import patch
from urllib.parse import urlencode

from dean import clock, public_refresh, public_schedule as sources, public_sync, public_transport
from dean.access_paths import login_url
from dean.db import connect
from dean.domain import DomainError
from tests.test_dean import DemoFixture
from tests.test_night_o import TEMPLATE, parse_ics
from tests.test_public_schedule import FIRST_ID, GROUP, MONDAY, SOURCE, grid

SECOND_GROUP = "fvs/526-1"
WEEK = "2026-11-23"
MOMENT = datetime.fromisoformat("2026-11-24T00:00:00+00:00")
ACTIVE = {"DEAN_TUSUR_LIVE": "1", "DEAN_TUSUR_AUTO_GROUPS": SECOND_GROUP, "DEAN_TUSUR_AUTO_PUBLISH": "1"}


def group_page(source, group, week=None):
    return "/universities/group?" + urlencode({"source": source, "group": group, "week": week or ""})


class RemainingFunctionsTest(DemoFixture):
    def test_guest_catalog_filters_group_pages_and_registry_rooms(self):
        guest = self.client()
        self.assertIn("Посмотреть расписание без входа", guest.request("/login")[2])
        self.assertIn("Открыть каталог вузов", guest.request("/guest")[2])
        self.assertEqual(guest.request("/schedule")[1]["Location"], login_url("/schedule"))
        self.assertEqual(guest.request("/universities", {"q": "516-1"})[0], 405)
        directory = "/universities?" + urlencode({"q": "Россия Томск 516-1"})
        html = guest.request(directory)[2]
        self.assertIn("516-1", html)
        self.assertNotIn("526-1", html)
        self.assertNotIn("Сохранённый снимок ·", html)
        route = group_page(SOURCE, GROUP, MONDAY)
        self.assertIn("Для этой даты пока нет подтверждённых данных.", guest.request(route)[2])
        with closing(connect(self.path)) as db:
            week = sources.week_scope(SOURCE, GROUP, MONDAY)
            public_sync.publish(db, public_sync.stage_sample(db, week))
        html = guest.request(route)[2]
        self.assertIn("08:50–10:25", html)
        self.assertIn("Сохранённый снимок", html)
        self.assertNotIn("ДЕМО ·", html)
        self.assertNotIn("note", html)
        self.assertEqual(guest.request(group_page(SOURCE, "нет такой"))[0], 404)
        guap = group_page("guap-reference", "7906")
        html = guest.request(guap)[2]
        self.assertIn("Привязка к датам пока не подтверждена.", html)
        room = "/universities/room?source=guap-reference&group=7906&room=guap-gastello"
        self.assertIn("Официальный источник", guest.request(room)[2])
        self.assertEqual(guest.request(room.replace("guap-gastello", "room313"))[0], 404)

    def test_refresh_preserves_notes_and_updates_calendar_subscription(self):
        student, guest = self.client("student-a"), self.client()
        week = sources.week_scope(SOURCE, SECOND_GROUP, WEEK)
        with closing(connect(self.path)) as db, patch.dict("os.environ", ACTIVE):
            with clock.at(MOMENT):
                first = public_refresh.run_once(db, read=lambda *_: (grid(week), '"version-1"'))
                self.assertEqual(first["result"], "published")
                skipped = public_refresh.run_once(db, read=lambda *_: self.fail("Grace period ignored"))
                self.assertEqual(skipped["result"], "nothing_due")
            context = "/schedule/context"
            fields = student.fields(context, context)
            self.assertEqual(student.request(context, {**fields, "selection": SOURCE + "|" + SECOND_GROUP})[0], 303)
            lesson = sources.lesson_id(week, FIRST_ID)
            view = "/schedule/public/" + lesson
            note = view + "/note"
            self.assertEqual(student.request(note, {**student.fields(view, note), "body": "Моя заметка"})[0], 303)
            issue = "/calendar/export/subscription"
            fields = student.fields("/calendar/export", issue)
            response = student.request(issue, {**fields, "first": WEEK, "last": "2026-11-29", "scope": "study"})
            self.assertEqual(response[0], 200)
            feed = search(r"(/calendar/feed/[A-Za-z0-9_-]{43})", response[2])[1]
            original = parse_ics(guest.request(feed)[2])
            self.assertEqual(len(original), 1)
            uid = next(iter(original))
            self.assertEqual(original[uid]["DTSTART"], "20261123T015000Z")
            with clock.at(MOMENT + timedelta(hours=7)):
                def moved(scope, etag):
                    self.assertEqual(scope, week)
                    self.assertEqual(etag, '"version-1"')
                    return grid(week, start="09:05"), '"version-2"'
                second = public_refresh.run_once(db, read=moved)
                self.assertEqual(second["diff"]["changed"], 1)
            self.assertEqual(self.db_one("SELECT count(*) FROM public_lesson"), 1)
            self.assertIn("Моя заметка", student.request(view)[2])
            route = group_page(SOURCE, SECOND_GROUP, WEEK)
            html = guest.request(route)[2]
            self.assertIn("09:05–10:25", html)
            self.assertNotIn("Моя заметка", html)
            changed = parse_ics(guest.request(feed)[2])
            self.assertEqual(list(changed), [uid])
            self.assertEqual(changed[uid]["DTSTART"], "20261123T020500Z")
            self.assertIn("09:05", student.request("/schedule/raspay?date=" + WEEK)[2])

    def test_incomplete_automatic_response_does_not_replace_snapshot(self):
        week = sources.week_scope(SOURCE, SECOND_GROUP, WEEK)
        with closing(connect(self.path)) as db, patch.dict("os.environ", ACTIVE), clock.at(MOMENT):
            public_sync.publish(db, public_sync.stage_sample(db, week))
            count = db.execute("SELECT count(*) FROM public_lesson").fetchone()[0]
            self.assertGreater(count, 1)
            changed = public_refresh.run_once(db, read=lambda *_: (grid(week), '"partial"'))
            self.assertEqual(changed["result"], "review_required")
            self.assertEqual(public_sync.week_row(db, week)["provenance"], "snapshot")
            self.assertEqual(db.execute("SELECT count(*) FROM public_lesson").fetchone()[0], count)
            with clock.at(MOMENT + timedelta(hours=7)):
                def blocked(*_):
                    raise public_transport.FetchError("dns")
                self.assertEqual(public_refresh.run_once(db, read=blocked)["result"], "deferred")
            self.assertEqual(public_sync.week_row(db, week)["revision"], 1)

    def test_a_fresh_reversion_cannot_publish_an_older_preview_after_304(self):
        week = sources.week_scope(SOURCE, SECOND_GROUP, WEEK)
        with closing(connect(self.path)) as db, patch.dict("os.environ", ACTIVE):
            with clock.at(MOMENT):
                original = public_sync.stage_live(db, week, lambda *_: (grid(week), '"1"'))
                public_sync.publish(db, original)
            with clock.at(MOMENT + timedelta(hours=7)):
                stale = public_sync.stage_live(db, week, lambda *_: (grid(week, start="09:00"), '"2"'))
            with clock.at(MOMENT + timedelta(hours=14)):
                self.assertEqual(public_refresh.run_once(db, read=lambda *_: (grid(week), '"3"'))["result"], "unchanged")
                with self.assertRaises(DomainError):
                    public_sync.publish(db, stale)
            with clock.at(MOMENT + timedelta(hours=21)):
                def conditional(scope, etag):
                    self.assertEqual(etag, '"3"')
                    return None, etag
                self.assertEqual(public_refresh.run_once(db, read=conditional)["result"], "unchanged")
            self.assertEqual(public_sync.week_row(db, week)["revision"], 1)

    def test_new_tusur_weeks_are_bounded_and_have_no_invented_sample(self):
        week = sources.week_scope(SOURCE, GROUP, "2026-12-07")
        self.assertTrue(week.url.endswith("?week_id=853"))
        with self.assertRaises(DomainError):
            sources.week_scope(SOURCE, SECOND_GROUP, "2026-12-07")
        with closing(connect(self.path)) as db, self.assertRaises(DomainError):
            public_sync.stage_sample(db, week)
        guest_html = self.client().request(group_page(SOURCE, GROUP, "2026-12-07"))[2]
        self.assertIn("Для этой даты пока нет подтверждённых данных.", guest_html)

    def test_import_preview_excludes_duplicate_rows_and_displays_conflicts(self):
        student, other = self.client("student-a"), self.client("student-b")
        data = TEMPLATE + (
            "sql,Базы данных,Лекция,2026-11-19T08:30,2026-11-19T10:00,Демо А-100,active\n"
            "sql,Строка для пропуска,Лекция,2026-11-19T12:00,2026-11-19T13:00,,active\n"
            "algorithm,Алгоритмы,Практика,2026-11-19T09:00,2026-11-19T10:30,,active\n"
        )
        route = "/calendar/import/csv"
        answer = student.request(route, {**student.fields("/calendar/import", route), "data": data})
        self.assertEqual(answer[0], 303)
        detail = answer[1]["Location"]
        self.assertIn("Ключ встречается в нескольких строках", student.request(detail)[2])
        exclude = detail + "/exclude"
        fields = {**student.fields(detail, exclude), "line": "3", "excluded": "1"}
        self.assertEqual(other.request(exclude, {**fields, "csrf": other.fields("/calendar/import", route)["csrf"]})[0], 404)
        self.assertEqual(student.request(exclude, fields)[0], 303)
        html = student.request(detail)[2]
        self.assertIn("К импорту 2 из 3", html)
        self.assertIn("Строка исключена из импорта", html)
        self.assertIn("По времени пересекается", html)
        self.assertEqual(self.db_one("SELECT count(*) FROM imported_event"), 0)
        confirm = detail + "/confirm"
        self.assertEqual(student.request(confirm, student.fields(detail, confirm))[0], 303)
        self.assertEqual(self.db_one("SELECT count(*) FROM imported_event"), 2)
        self.assertNotIn("Строка для пропуска", student.request("/schedule?date=2026-11-19")[2])
