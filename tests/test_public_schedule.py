from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from dataclasses import replace
from datetime import datetime, timedelta
from html import escape
import socket
from unittest.mock import patch

from dean import clock, public_schedule as catalog, public_sync, public_transport, public_tusur
from dean.db import connect, write
from dean.domain import DomainError, timestamp
from dean.schedule import local_value
from dean.seed import seed_id
from tests.test_dean import DemoFixture

SOURCE = "tusur-timetable"
GROUP = "fvs/516-1"
MONDAY = "2026-11-16"
SELECTION = SOURCE + "|" + GROUP
FIRST_ID = "687069"
TIME = datetime.fromisoformat("2026-11-16T01:35:00+00:00")


def grid(week, subject="Проверочный предмет", start="08:50", duplicate=False):
    headers = "".join(f"<th>{name}, {day} нояб.</th>" for day, name in
                      enumerate(("пн", "вт", "ср", "чт", "пт", "сб"), week.monday.day))
    marker = escape(week.group["path"] + "?week_id=" + week.info["id"])
    lesson = (
        '<div class="lesson-cell"><div class="training">'
        f'<span class="discipline js-lesson-info" data-lesson-id="{FIRST_ID}">{subject}</span>'
        '<span class="kind">Лекция</span><span class="auditoriums">'
        '<a href="/buildings/f/auditoriums/121">ф 121</a></span>'
        '<span class="teacher">PRIVATE_FIXTURE_MARKER</span></div></div>'
    )
    cells = "".join(
        f'<td class="lesson_cell day_{day}"><div class="lessons-wrapper">'
        f'{(lesson * (2 if duplicate else 1)) if day == 1 else ""}</div></td>'
        for day in range(1, 7)
    )
    return (f'<h1>Расписание занятий группы {week.group["label"]}</h1><a href="{marker}">Неделя 2026</a>'
            f'<table class="table-lessons"><tr><th></th>{headers}</tr>'
            f'<tr><th class="time"><span>{start}</span><span>10:25</span></th>{cells}</tr></table>').encode()


class PublicScheduleTest(DemoFixture):
    def setUp(self):
        super().setUp()
        self.scope = catalog.week_scope(SOURCE, GROUP, MONDAY)
        self.event_id = catalog.lesson_id(self.scope, FIRST_ID)
        self.lesson = "/schedule/public/" + self.event_id

    def select(self, client, group=SELECTION):
        page = "/schedule/context"
        status, headers, _ = client.request(page, {**client.fields(page, page), "selection": group})
        self.assertEqual((status, headers["Location"]), (303, "/schedule"))

    def publish_sample(self, week=None):
        with closing(connect(self.path)) as db:
            preview = public_sync.stage_sample(db, week or self.scope)
            return public_sync.publish(db, preview)

    def stage_test_events(self, db, events):
        with write(db):
            return public_sync.stage_in_transaction(db, self.scope, events, "synthetic", timestamp())

    def test_published_weeks_in_cabinet_with_room_services_and_empty_state(self):
        self.assertEqual(self.publish_sample()["added"], 21)
        next_week = catalog.week_scope(SOURCE, GROUP, "2026-11-23")
        self.assertEqual(self.publish_sample(next_week)["added"], 17)
        demo_group = self.db_one("SELECT group_id FROM account WHERE username='student-a'")
        student = self.client("student-a")
        self.select(student)
        page = student.request("/schedule?view=week&date=" + MONDAY)[2]
        self.assertIn("ТУСУР", page)
        self.assertIn("Сохранённый снимок официального расписания", page)
        self.assertIn("Asia/Tomsk", page)
        self.assertIn("12-я неделя · чётная", page)
        self.assertIn("08:50–10:25", page)
        self.assertIn("ф 121", page)
        self.assertIn("2026-11-17", page)
        self.assertIn("13-я неделя · нечётная", student.request("/schedule?view=week&date=2026-11-23")[2])
        self.assertIn("Данные на этот период не получены",
                      student.request("/schedule?view=week&date=2026-11-30")[2])
        self.assertIn("Имена преподавателей исключены", student.request(self.lesson)[2])
        status, _, room = student.request("/schedule/rooms/f/121")
        self.assertEqual(status, 200)
        self.assertIn("Вершинина, 74", room)
        self.assertIn("Красноармейская, 146", student.request("/student-services")[2])
        self.assertIn("Учебный стенд · вымышленные данные", student.request("/grades")[2])
        self.assertEqual(self.db_one("SELECT group_id FROM account WHERE username='student-a'"), demo_group)
        self.assertIn("Ада Кремнева", student.request(f"/schedule/{seed_id('a1')}")[2])
        self.assertEqual(student.request("/schedule/rooms/f/000")[0], 404)

    def test_month_loads_public_coverage_without_a_query_per_day(self):
        self.publish_sample()
        a = self.client("student-a")
        self.select(a)
        statements = []

        def traced_connect(*args, **kwargs):
            db = connect(*args, **kwargs)
            db.set_trace_callback(statements.append)
            return db

        with patch("dean.web.connect", side_effect=traced_connect):
            status, _, body = a.request(f"/schedule?view=month&date={MONDAY}")
        self.assertEqual(status, 200)
        self.assertIn('class="dean-schedule-month-cell" data-day-tone="unavailable"', body)
        self.assertIn('class="dean-schedule-month-cell" data-day-tone="lessons"', body)
        coverage_queries = [sql for sql in statements if "FROM PUBLIC_WEEK" in sql.upper()]
        self.assertLessEqual(len(coverage_queries), 3)

    def test_note_retry_restart_private_search_and_context_switch(self):
        self.publish_sample()
        a = self.client("student-a")
        b = self.client("student-b")
        self.select(a)
        note = self.lesson + "/note"
        self.assertEqual(a.request(note, {"body": "synthetic"})[0], 403)
        form = a.fields(self.lesson, note)
        detail = "Синтетическая заметка & <b>только A</b>"
        self.assertEqual(a.request(note, {**form, "body": detail, "remind": "1"})[0], 303)
        self.assertEqual(a.request(note, {**form, "body": detail, "remind": "1"})[0], 303)
        self.assertEqual(self.db_one("SELECT count(*) FROM public_note"), 1)
        self.assertIn("&lt;b&gt;", self.read_after_restart(a, self.lesson))
        self.assertIn(self.lesson, a.request("/search?q=синтетическая&kind=note")[2])
        self.assertEqual(b.request(self.lesson)[0], 404)
        self.select(b)
        self.assertNotIn("только A", b.request(self.lesson)[2])
        self.assertNotIn(self.lesson, b.request("/search?q=синтетическая&kind=note")[2])
        self.select(a, "")
        self.assertEqual(a.request(self.lesson)[0], 404)
        self.assertNotIn(self.lesson, a.request("/search?q=синтетическая")[2])
        self.select(a)
        self.assertEqual(self.publish_sample()["added"], 0)
        self.assertIn("только A", a.request(self.lesson)[2])

    def test_actual_groups_shared_external_id_and_private_overlay(self):
        first = catalog.week_scope(SOURCE, GROUP, MONDAY)
        other = catalog.week_scope(SOURCE, "fvs/526-1", MONDAY)
        third = catalog.week_scope(SOURCE, "fsu/426-3", MONDAY)
        for week, count in ((first, 21), (other, 21), (third, 18)):
            self.assertEqual(self.publish_sample(week)["added"], count)
        self.assertEqual(self.db_one("SELECT count(*) FROM public_lesson WHERE external_id='677600'"), 2)
        first_url = "/schedule/public/" + catalog.lesson_id(first, "677600")
        other_url = "/schedule/public/" + catalog.lesson_id(other, "677600")
        a = self.client("student-a")
        self.select(a)
        form = a.fields(first_url, first_url + "/note")
        self.assertEqual(a.request(first_url + "/note", {**form, "body": "Секрет группы 516-1"})[0], 303)
        self.select(a, SOURCE + "|fvs/526-1")
        self.assertEqual(a.request(first_url)[0], 404)
        self.assertNotIn("Секрет группы", a.request(other_url)[2])
        self.assertNotIn("Секрет группы", a.request("/search?q=секрет&kind=note")[2])
        self.select(a, SOURCE + "|fsu/426-3")
        self.assertIn("Факультет систем управления", a.request("/schedule?date=" + MONDAY)[2])
        self.select(a)
        self.assertIn("Секрет группы 516-1", a.request(first_url)[2])

    def test_source_facts_control_sample_for_three_groups_two_weeks(self):
        points = (
            (GROUP, MONDAY, "687069", 16, "08:50", "10:25", "Математика", "Лекция", ("ф 121",)),
            (GROUP, MONDAY, "681268", 18, "13:15", "14:50", "ОРГ", "Практика", ("рк 310",)),
            (GROUP, "2026-11-23", "677604", 24, "15:00", "16:35", "Иностранный язык", "Практика", ("гк 402", "гк 403")),
            (GROUP, "2026-11-23", "677323", 23, "13:15", "14:50", "Информатика", "Лабораторная работа", ("ф 327", "ф 329")),
            ("fvs/526-1", MONDAY, "687071", 16, "08:50", "10:25", "Математика", "Практика", ("ф 128",)),
            ("fvs/526-1", MONDAY, "681369", 20, "15:00", "16:35", "ПИТР", "Практика", ("рк 337а",)),
            ("fvs/526-1", "2026-11-23", "677331", 23, "13:15", "14:50", "Инженерная графика", "Практика", ("рк 502",)),
            ("fvs/526-1", "2026-11-23", "678032", 25, "08:50", "10:25", "Информатика", "Лабораторная работа", ("ф 327", "ф 329")),
            ("fsu/426-3", MONDAY, "681732", 17, "08:50", "10:25", "Информатика", "Практика", ("ф 430",)),
            ("fsu/426-3", MONDAY, "681622", 20, "08:50", "10:25", "История России", "Лекция", ("гк 428",)),
            ("fsu/426-3", "2026-11-23", "681731", 27, "13:15", "14:50", "Информатика", "Лабораторная работа", ("ф 432а", "ф 432б")),
            ("fsu/426-3", "2026-11-23", "688958", 28, "16:45", "18:20", "ОРГ", "Лекция", ()),
        )
        for group, monday, external_id, day, start, end, subject, kind, rooms in points:
            with self.subTest(group=group, monday=monday, external_id=external_id):
                scope = catalog.week_scope(SOURCE, group, monday)
                event = next(row for row in catalog.sample_events(scope) if row.external_id == external_id)
                begins = local_value(event.starts_at, "Asia/Tomsk")
                finishes = local_value(event.ends_at, "Asia/Tomsk")
                self.assertEqual((begins.day, begins.strftime("%H:%M"), finishes.strftime("%H:%M")), (day, start, end))
                self.assertEqual((event.subject, event.kind, tuple(room[0] for room in event.rooms)), (subject, kind, rooms))

    def test_public_calendar_shows_overlap_with_private_event(self):
        self.publish_sample()
        a = self.client("student-a")
        b = self.client("student-b")
        self.select(a)
        self.select(b)
        url = "/events/new"
        fields = {**a.fields(url, url), "title": "Личное испытание конфликта",
                  "starts": MONDAY + "T04:55", "ends": MONDAY + "T05:20"}
        response = a.request(url, fields)
        self.assertEqual(response[0], 303)
        page = a.request("/schedule?view=day&date=" + MONDAY)[2]
        self.assertIn("Личное испытание конфликта", page)
        self.assertEqual(page.count(">Пересечение<"), 2)
        self.assertNotIn("Личное испытание конфликта", b.request("/schedule?view=day&date=" + MONDAY)[2])

    def test_admin_mount_requires_explicit_grant_and_confirms_diff(self):
        staff = self.client("staff")
        student = self.client("student-a")
        page = "/staff/public"
        self.assertEqual(staff.request(page)[0], 403)
        self.assertEqual(student.request(page)[0], 403)
        self.assertEqual(staff.request("/schedule/context")[0], 403)
        with closing(connect(self.path)) as db:
            public_sync.allow_operator(db, "staff")
        form = staff.fields(page, page)
        untrusted = {**form, "mode": "snapshot", "scope": "untrusted|unlisted|" + MONDAY}
        self.assertEqual(staff.request(page, untrusted)[0], 422)
        result = staff.request(page, {**form, "mode": "snapshot", "scope": SELECTION + "|" + MONDAY})
        self.assertEqual(result[0], 303)
        detail = result[1]["Location"]
        self.assertIn("Новые · 21", staff.request(detail)[2])
        self.assertEqual(self.db_one("SELECT count(*) FROM public_lesson"), 0)
        self.assertEqual(staff.request(detail, staff.fields(detail, detail))[0], 303)
        self.assertEqual(self.db_one("SELECT count(*) FROM public_lesson"), 21)
        self.assertEqual(student.request(detail)[0], 403)

    def test_moves_and_explicit_cancellation_keep_note_and_notify_once(self):
        self.publish_sample()
        a = self.client("student-a")
        b = self.client("student-b")
        self.select(a)
        self.select(b)
        note = self.lesson + "/note"
        a.request(note, {**a.fields(self.lesson, note), "body": "Приватно", "remind": "1"})
        with clock.at(TIME):
            self.assertIn("Скоро публичная пара", a.request("/notifications")[2])
            self.assertEqual(self.db_one("SELECT count(*) FROM notification WHERE kind='public_reminder'"), 1)
            self.assertNotIn("Скоро публичная пара", b.request("/notifications")[2])
        entries = catalog.sample_events(self.scope)
        event = next(row for row in entries if row.external_id == FIRST_ID)
        moved = replace(event, starts_at=timestamp(TIME + timedelta(hours=1)), ends_at=timestamp(TIME + timedelta(hours=2)))
        added = replace(event, external_id="999991")
        records = tuple(moved if item == event else item for item in entries) + (added,)
        with closing(connect(self.path)) as db:
            preview = self.stage_test_events(db, records)
            report = public_sync.publish(db, preview)
            self.assertEqual((report["changed"], report["added"]), (1, 1))
            self.assertEqual(public_sync.publish(db, preview)["changed"], 0)
        self.assertIn("Приватно", a.request(self.lesson)[2])
        self.assertIn("09:35", a.request(self.lesson)[2])
        self.assertIn("Публичная пара обновлена", a.request("/notifications")[2])
        self.assertNotIn("Публичная пара обновлена", b.request("/notifications")[2])
        with closing(connect(self.path)) as db:
            cancelled = replace(moved, cancelled=True)
            records = tuple(cancelled if item == moved else item for item in records)
            preview = self.stage_test_events(db, records)
            self.assertEqual(public_sync.publish(db, preview)["cancelled"], 1)
        self.assertIn("Отменено источником", a.request(self.lesson)[2])
        self.assertIn("Приватно", a.request(self.lesson)[2])
        self.assertNotIn(self.lesson, a.request("/schedule?view=week&date=" + MONDAY)[2])
        self.assertEqual(self.db_one("SELECT count(*) FROM notification WHERE kind='public_change'"), 2)

    def test_ambiguous_and_partial_updates_never_replace_published_data(self):
        self.publish_sample()
        baseline = catalog.sample_events(self.scope)
        ambiguous = replace(baseline[0], subject="Другой предмет")
        with closing(connect(self.path)) as db:
            first = self.stage_test_events(db, (ambiguous, *baseline[1:]))
            self.assertEqual(public_sync.describe(db, first)["ambiguous"], 1)
            with self.assertRaises(DomainError):
                public_sync.publish(db, first)
            missing = self.stage_test_events(db, baseline[1:])
            self.assertEqual(public_sync.describe(db, missing)["missing"], 1)
            with self.assertRaises(DomainError):
                public_sync.publish(db, missing)
            with self.assertRaises(DomainError):
                self.stage_test_events(db, ())
        self.assertEqual(self.db_one("SELECT count(*) FROM public_lesson"), 21)
        self.assertEqual(self.db_one("SELECT revision FROM public_week"), 1)

    def test_one_concurrent_publish_per_revision(self):
        with closing(connect(self.path)) as db:
            previews = [public_sync.stage_sample(db, self.scope) for _ in range(2)]

        def apply(key):
            with closing(connect(self.path)) as db:
                try:
                    public_sync.publish(db, key)
                except DomainError as exc:
                    return exc.status
                return 200

        with ThreadPoolExecutor(max_workers=2) as workers:
            self.assertEqual(sorted(workers.map(apply, previews)), [200, 409])
        self.assertEqual(self.db_one("SELECT count(*) FROM public_lesson"), 21)

    def test_html_parser_scopes_and_only_keeps_allowlisted_fields(self):
        events = public_tusur.parse(self.scope, grid(self.scope))
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].external_id, FIRST_ID)
        self.assertEqual(events[0].starts_at, "2026-11-16T01:50:00+00:00")
        self.assertNotIn("PRIVATE_FIXTURE_MARKER", catalog.event_json(events))
        source = grid(self.scope)
        for broken in (source.replace(b"2026", b"2025"), source.replace(b"850", b"851"),
                       source.replace(b"day_2", b"day_3"), source.replace(b"516-1", b"526-1"),
                       b"<html>captcha</html>", grid(self.scope, duplicate=True)):
            with self.assertRaises(ValueError):
                public_tusur.parse(self.scope, broken)

    def test_unavailable_live_and_network_errors_leave_sample_published(self):
        self.publish_sample()
        with closing(connect(self.path)) as db:
            with self.assertRaises(DomainError) as error:
                public_sync.stage_live(db, self.scope, lambda *_: self.fail("Live disabled"))
            self.assertEqual(error.exception.status, 409)
            moment = TIME
            with patch.dict(catalog.TUSUR, live_enabled=True):
                for response in (public_transport.FetchError("timeout"),
                                 public_transport.FetchError("upstream_busy", TIME + timedelta(minutes=10)),
                                 b"<html>broken</html>", public_transport.FetchError("access_blocked")):
                    def read(*_):
                        if isinstance(response, Exception):
                            raise response
                        return response, None
                    with clock.at(moment), self.assertRaises(DomainError):
                        public_sync.stage_live(db, self.scope, read)
                    moment += timedelta(hours=2)
                with clock.at(moment), self.assertRaises(DomainError):
                    public_sync.stage_live(db, self.scope, lambda *_: self.fail("403 halted the route"))
            self.assertEqual(public_sync.week_row(db, self.scope)["provenance"], "snapshot")
            self.assertEqual(db.execute("SELECT count(*) FROM public_lesson").fetchone()[0], 21)

    def test_conditional_response_and_retry_after_do_not_change_publication(self):
        with closing(connect(self.path)) as db, patch.dict(catalog.TUSUR, live_enabled=True):
            with clock.at(TIME):
                preview = public_sync.stage_live(db, self.scope, lambda *_: (grid(self.scope), '"schema-ver"'))
                public_sync.publish(db, preview)
                published = dict(public_sync.week_row(db, self.scope))
                with self.assertRaises(DomainError):
                    public_sync.stage_live(db, self.scope, lambda *_: self.fail("Budget is active"))
            with clock.at(TIME + timedelta(hours=6, minutes=1)):
                def conditional(week, etag):
                    self.assertEqual(etag, '"schema-ver"')
                    return None, None
                self.assertIsNone(public_sync.stage_live(db, self.scope, conditional))
                self.assertEqual(published, dict(public_sync.week_row(db, self.scope)))
                self.assertEqual(db.execute("SELECT result FROM public_attempt ORDER BY rowid DESC").fetchone()[0],
                                 "not_modified")

    def test_dns_rejects_non_public_addresses_and_retry_after_parses(self):
        for address in ("127.0.0.1", "169.254.169.254", "10.1.1.2", "::ffff:127.0.0.1", "ff02::1"):
            info = [(socket.AF_INET, socket.SOCK_STREAM, 0, "", (address, 443))]
            with patch.object(socket, "getaddrinfo", return_value=info), self.assertRaises(public_transport.FetchError):
                public_transport.allowed_addresses("timetable.tusur.ru")
        self.assertEqual(public_transport.retry_time("120", TIME), TIME + timedelta(minutes=2))
        self.assertEqual(public_transport.retry_time("Mon, 16 Nov 2026 02:35:00 GMT", TIME), TIME + timedelta(hours=1))
        self.assertEqual(public_transport.retry_time("unparseable", TIME), TIME + timedelta(hours=1))
