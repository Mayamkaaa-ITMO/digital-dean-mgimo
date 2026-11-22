from contextlib import closing
from dataclasses import replace
from datetime import date, datetime, timedelta
from urllib.parse import urlencode
from uuid import uuid4

from dean import public_schedule, public_sync, schedule_display
from dean.agenda import Entry
from dean.db import connect, write
from dean.domain import timestamp
from tests.test_dean import DemoFixture
from tests.test_night_l import FIRST, OTHER, START, choose


class CalendarToolsTest(DemoFixture):
    def test_daily_breaks_only_between_adjacent_lessons(self):
        day = date(2026, 11, 22)
        first = Entry("first", "Физика", "2026-11-22T06:00:00+00:00", "2026-11-22T07:30:00+00:00",
                      "lesson", "/schedule/first")
        second = Entry("second", "Практикум", "2026-11-22T08:30:00+00:00", "2026-11-22T10:00:00+00:00",
                       "lesson", "/schedule/second")
        deadline = Entry("deadline", "Сдать расчёт", "2026-11-22T20:00:00+00:00", None, "task", "/tasks/deadline")
        meetup = Entry("meetup", "Встреча", "2026-11-22T08:00:00+00:00", "2026-11-22T08:05:00+00:00",
                       "event", "/events/meetup")

        def render(*items):
            return schedule_display.day_section(day, items, day, day, "/schedule", "", "Europe/Moscow", set(),
                                                "", False, gaps=True)

        self.assertNotIn("dean-schedule-gap", render(first, deadline))
        self.assertNotIn("dean-schedule-gap", render(first, meetup, second, deadline))
        page = render(first, second, deadline)
        self.assertEqual(page.count("dean-schedule-gap"), 1)
        self.assertIn("Перерыв 1 ч", page)
        self.assertIn("10:30–11:30", page)
        self.assertIn("Сдать расчёт", page)

    def setup_public(self):
        scope = public_schedule.week_scope(*FIRST.split("|"), START)
        events = public_schedule.sample_events(scope)
        with closing(connect(self.path)) as db:
            public_sync.publish(db, public_sync.stage_sample(db, scope))
        return scope, events

    def test_change_history_and_stale_private_adjustment(self):
        scope, events = self.setup_public()
        a, b = self.client("student-a"), self.client("student-b")
        choose(a, FIRST)
        choose(b, FIRST)
        change = "/schedule/changes"
        self.assertEqual(a.request(change, a.fields(change, change))[0], 303)
        event = events[0]
        lesson_id = public_schedule.lesson_id(scope, event.external_id)
        detail = "/schedule/public/" + lesson_id
        adjust = detail + "/adjustment/save"
        form = a.fields(detail, adjust)
        local = {**form, "starts": START + "T22:00", "ends": START + "T23:00", "room": "Закрытый текст A"}
        self.assertEqual(a.request(adjust, local)[0], 303)
        day = "/schedule?view=day&date=" + START
        self.assertIn("Личная поправка", a.request(day)[2])
        self.assertIn("Закрытый текст A", a.request(day)[2])
        self.assertNotIn("Закрытый текст A", b.request(day)[2])
        stale = a.fields(detail, adjust)
        moved = replace(event, starts_at=timestamp(datetime.fromisoformat(event.starts_at)+timedelta(minutes=5)),
                        ends_at=timestamp(datetime.fromisoformat(event.ends_at)+timedelta(minutes=5)))
        with closing(connect(self.path)) as db:
            with write(db):
                key = public_sync.stage_in_transaction(db, scope, (moved, *events[1:]), "synthetic", START)
            self.assertEqual(public_sync.publish(db, key)["changed"], 1)
        self.assertEqual(a.request(adjust, stale)[0], 409)
        self.assertIn("Поправка ждёт сверки", a.request(day)[2])
        self.assertIn("Было:", a.request(change)[2])
        self.assertIn("Стало:", a.request(change)[2])
        self.assertIn("Публичная пара обновлена", a.request("/notifications")[2])
        self.assertNotIn("Публичная пара обновлена", b.request("/notifications")[2])
        remove = detail + "/adjustment/remove"
        self.assertEqual(a.request(remove, a.fields(detail, remove))[0], 303)
        self.assertEqual(self.db_one("SELECT count(*) FROM calendar_adjustment"), 0)

    def test_month_list_persisted_filters_and_unconfirmed_dates(self):
        self.setup_public()
        student = self.client("student-a")
        choose(student, FIRST)
        route = "/schedule?view=month&date=" + START
        self.assertIn("Открыть день", student.request(route)[2])
        self.assertIn("Данные на этот день не получены.", student.request(route)[2])
        action = "/schedule/filters"
        filters = student.fields(route, action)
        self.assertEqual(student.request(action, {**filters, "visibility": "personal"})[0], 303)
        self.assertNotIn("/schedule/public/", student.request("/schedule?view=list&date=" + START)[2])
        choose(student, OTHER)
        self.assertEqual(student.request(action, {**filters, "visibility": "all"})[0], 409)
        self.assertEqual(student.request("/schedule?view=list&date=" + START)[0], 200)

    def test_calendar_position_restored_across_sessions_and_scoped_to_context(self):
        student, second_session = self.client("student-a"), self.client("student-a")
        other = self.client("student-b")
        day = (self.today + timedelta(days=4)).isoformat()
        route = "/schedule?view=day&date=" + day
        self.assertEqual(student.request(route)[0], 200)
        html = second_session.request("/schedule")[2]
        self.assertIn('data-calendar-view="day" aria-current="page"', html)
        self.assertIn(f'name="date" value="{day}"', html)
        self.assertIn('data-calendar-view="week" aria-current="page"', other.request("/schedule")[2])

        choose(student, FIRST)
        self.assertIn('data-calendar-view="week" aria-current="page"', student.request("/schedule")[2])
        public_day = (self.today + timedelta(days=7)).isoformat()
        self.assertEqual(student.request("/schedule?view=month&date=" + public_day)[0], 200)
        choose(student, "")
        self.assertIn(f'name="date" value="{day}"', student.request("/schedule")[2])
        choose(student, FIRST)
        public_html = student.request("/schedule")[2]
        self.assertIn('data-calendar-view="month" aria-current="page"', public_html)
        self.assertIn(f'name="date" value="{public_day}"', public_html)

    def test_clear_filters_persists_and_preserves_selected_period(self):
        student = self.client("student-a")
        route = "/schedule?view=day&date=" + self.today.isoformat()
        action = "/schedule/filters"
        filters = student.fields(route, action)
        self.assertEqual(student.request(action, {**filters, "kind": "lesson", "visibility": "study"})[0], 303)
        self.assertIn("Активные фильтры", student.request(route)[2])
        self.assertNotIn("Активные фильтры", student.request(route + "&kind=&subject=&visibility=all")[2])
        reset = action + "/reset"
        cleared = student.request(reset, student.fields(route, reset))
        self.assertEqual(cleared[0], 303)
        self.assertIn("view=day", cleared[1]["Location"])
        restored = self.client("student-a").request("/schedule")[2]
        self.assertNotIn("Активные фильтры", restored)
        self.assertIn('data-calendar-view="day" aria-current="page"', restored)

    def test_free_slots_rechecked_and_half_open_across_restarts(self):
        a, b = self.client("student-a"), self.client("student-b")
        query = urlencode({"first": "2027-01-05", "last": "2027-01-05",
                           "from": "08:00", "to": "11:00", "minutes": "60"})
        route = "/schedule/free?" + query
        self.assertIn("Свободно по выбранным данным", a.request(route)[2])
        form = a.fields(route, "/schedule/free")
        saved = a.request("/schedule/free", {**form, "title": "Мой первый интервал"})
        self.assertEqual(saved[0], 303)
        self.assertEqual(a.request("/schedule/free", {**form, "title": "Мой первый интервал"})[1]["Location"],
                         saved[1]["Location"])
        self.assertEqual(a.request("/schedule/free", {**form, "request_id": uuid4().hex})[0], 409)
        adjacent = a.fields(route, "/schedule/free")
        self.assertEqual(adjacent["starts"], form["ends"])
        self.assertEqual(a.request("/schedule/free", adjacent)[0], 303)
        self.assertIn("Мой первый интервал", self.read_after_restart(a, saved[1]["Location"]))
        self.assertEqual(b.request(saved[1]["Location"])[0], 404)
