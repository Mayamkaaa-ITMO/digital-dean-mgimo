from contextlib import closing
from dean.access_paths import login_url
from datetime import datetime, timedelta
from urllib.parse import urlencode
from zoneinfo import ZoneInfo

from dean import public_schedule, public_sync
from dean.clock import at
from dean.db import connect
from dean.seed import seed_id
from tests.test_dean import DemoFixture
from tests.test_public_universities import GUAP, MONDAY, POLYTECH, SUSU, TUSUR

ROOT = "/schedule/raspay"


class RaspayTest(DemoFixture):
    def day(self, offset=0):
        return (self.today + timedelta(days=offset)).isoformat()

    def page(self, slug="", day=None):
        return ROOT + slug + "?" + urlencode({"date": day or self.day()})

    def select(self, user, choice):
        route = ROOT + "/groups"
        response = user.request(route, {**user.fields(route, route), "selection": "|".join(choice)})
        self.assertEqual(response[0], 303)
        return response[1]["Location"]

    def test_second_view_and_legacy_switch_share_data(self):
        student = self.client("student-a")
        b = self.client("student-b")
        lesson = seed_id("a1")
        with at(datetime.combine(self.today, datetime.min.time(), ZoneInfo("Europe/Moscow"))):
            special = self.page()
            legacy = "/schedule?view=day&date=" + self.day()
            body = student.request(special)[2]
            self.assertIn("Первая пара", body)
            self.assertEqual(body.count('class="rp-pill'), 8)
            self.assertIn('aria-current="date"', body)
            self.assertIn("Перерыв между парами", body)
            self.assertIn("Дальше пар нет", body)
            self.assertIn("Исходный интерфейс", body)
            self.assertIn("Вид Raspay", student.request(legacy)[2])
            self.assertNotIn('href="/static/raspay.css"', student.request(legacy)[2])
            self.assertIn(lesson, student.request(legacy)[2])
            self.assertNotIn(lesson, b.request(special)[2])
            self.assertIn("В этот день пар нет", student.request(self.page(day="2030-01-01"))[2])
            self.assertIn("Обычная карточка пары", student.request(self.page("/lesson/" + lesson))[2])
        self.assertEqual(b.request(self.page("/lesson/" + lesson))[0], 404)
        self.assertEqual(self.client("staff").request(special)[0], 403)
        self.assertEqual(self.client().request(special)[1]["Location"], login_url(special))

    def test_active_and_past_timeline_states(self):
        student = self.client("student-a")
        for hours, minutes, expected in ((10, 15, "Сейчас идёт"), (12, 0, "Следующая пара"), (16, 0, "Пары закончились")):
            moment = datetime.fromisoformat(f"{self.day()}T{hours:02}:{minutes:02}:00+03:00")
            with at(moment):
                body = student.request(self.page())[2]
                self.assertIn(expected, body)
                if hours == 12:
                    self.assertIn("Прошедшие пары", body)
                    self.assertIn("data-rp-counter", body)

    def test_theme_gaps_validation_and_restart(self):
        student, other = self.client("student-a"), self.client("student-b")
        settings = self.page("/settings")
        gaps = ROOT + "/preferences/gaps"
        initial = student.fields(settings, gaps)
        self.assertIn('role="switch" aria-checked="true"', student.request(settings)[2])
        self.assertEqual(student.request(gaps, {**initial, "csrf": ""})[0], 403)
        self.assertEqual(student.request(gaps, initial)[0], 303)
        self.assertEqual(student.request(gaps, initial)[0], 409)
        self.assertNotIn("Перерыв между парами", student.request(self.page())[2])
        self.assertIn('role="switch" aria-checked="false"', self.read_after_restart(student, settings))
        self.assertIn('role="switch" aria-checked="true"', other.request(settings)[2])
        colors = ROOT + "/preferences/color"
        custom = student.fields(self.page("/colors/lecture"), colors)
        self.assertEqual(student.request(colors, {**custom, "hex": "не цвет"})[0], 422)
        self.assertEqual(student.request(colors, {**custom, "hex": "#3b53b1"})[0], 303)
        self.assertIn('fill="#3B53B1"', self.read_after_restart(student, self.page("/colors")))
        self.assertNotIn('fill="#3B53B1"', other.request(self.page("/colors"))[2])
        reset = ROOT + "/preferences/reset-colors"
        self.assertEqual(student.request(reset, student.fields(self.page("/colors"), reset))[0], 303)
        self.assertIn('fill="#63CDE3"', student.request(self.page("/colors"))[2])

    def test_real_context_and_optional_language_room(self):
        student = self.client("student-a")
        room = ROOT + "/preferences/room"
        choices = self.page("/room", self.day(1))
        fields = student.fields(choices, room)
        self.assertIn("ДЕМО · Б-102", student.request(choices)[2])
        self.assertEqual(student.request(room, {**fields, "room": "Другая аудитория"})[0], 422)
        picked = student.request(room, {**fields, "room": "ДЕМО · Б-102"})
        self.assertEqual(picked[0], 303)
        self.assertIn("Моя аудитория", student.request(self.page(day=self.day(1)))[2])
        stale = student.fields(choices, room)
        self.assertEqual(self.select(student, GUAP), ROOT)
        unknown, _, body = student.request(self.page(day=MONDAY))
        self.assertEqual(unknown, 200)
        self.assertIn("Данные на этот день не получены.", body)
        self.assertIn("Справочная сетка группы", body)
        self.assertNotIn('class="rp-row rp-lesson"', body)
        self.assertEqual(student.request(room, stale)[0], 409)
        self.assertIn("нет подтверждённых вариантов", student.request(choices)[2])
        self.assertEqual(self.select(student, ()), ROOT)
        self.assertIn("Моя аудитория", student.request(self.page(day=self.day(1)))[2])

    def test_personal_hiding_and_notes_preserve_original_calendar(self):
        student = self.client("student-a")
        lesson = seed_id("a1")
        root = ROOT + "/lesson/" + lesson
        detail = self.page("/lesson/" + lesson)
        note = root + "/note"
        text = "Короткая личная заметка <пара>"
        saved = student.request(note, {**student.fields(detail, note), "body": text})
        self.assertEqual(saved[0], 303)
        self.assertIn("Короткая личная заметка &lt;пара&gt;", student.request(saved[1]["Location"])[2])
        self.assertIn("Короткая личная заметка", self.read_after_restart(student, "/schedule/" + lesson))
        hide = root + "/hide"
        confirmation = self.page("/lesson/" + lesson + "/hide")
        self.assertIn("Скрыть пару?", student.request(confirmation)[2])
        self.assertEqual(self.db_one("SELECT count(*) FROM schedule_display_hidden"), 0)
        self.assertEqual(student.request(hide, student.fields(confirmation, hide))[0], 303)
        body = self.read_after_restart(student, self.page())
        self.assertIn("Скрытые пары", body)
        self.assertNotIn('class="rp-row rp-lesson" href="' + root, body)
        self.assertIn("/schedule/" + lesson, student.request("/schedule?date=" + self.day())[2])
        restore = root + "/restore"
        self.assertEqual(student.request(restore, student.fields(restore, restore))[0], 303)
        self.assertIn('class="rp-row rp-lesson" href="' + root, student.request(self.page())[2])

    def test_published_rooms_notes_and_time_adjustments_use_existing_storage(self):
        a, b = self.client("student-a"), self.client("student-b")
        week = public_schedule.week_scope(*POLYTECH, MONDAY)
        with closing(connect(self.path)) as db:
            public_sync.publish(db, public_sync.stage_sample(db, week))
        self.select(a, POLYTECH)
        self.select(b, POLYTECH)
        event = next(item for item in public_schedule.sample_events(week) if item.starts_at.startswith(MONDAY))
        lesson = public_schedule.lesson_id(week, event.external_id)
        root = ROOT + "/lesson/" + lesson
        detail = self.page("/lesson/" + lesson, MONDAY)
        body = a.request(detail)[2]
        self.assertIn("Карточка дисциплины", body)
        self.assertIn("Сохранённый снимок официального расписания", body)
        self.assertIn(lesson, a.request(self.page(day=MONDAY))[2])
        note = root + "/note"
        self.assertEqual(a.request(note, {**a.fields(detail, note), "body": "Заметка к источнику"})[0], 303)
        self.assertIn("Заметка к источнику", a.request("/schedule/public/" + lesson)[2])
        self.assertNotIn("Заметка к источнику", b.request(detail)[2])
        edit = root + "/time"
        fields = a.fields(edit, edit)
        changed = {**fields, "starts": MONDAY + "T20:40", "ends": MONDAY + "T21:30"}
        self.assertEqual(a.request(edit, changed)[0], 303)
        self.assertIn("Личная поправка действует", a.request(detail)[2])
        self.assertIn("20:40", a.request("/schedule?view=day&date=" + MONDAY)[2])
        self.assertNotIn("20:40", b.request(detail)[2])
        self.select(a, TUSUR)
        self.assertEqual(a.request(detail)[0], 404)
        self.assertEqual(a.request(edit, changed)[0], 409)
        self.select(a, SUSU)
        self.assertIn("Asia/Yekaterinburg", a.request(self.page(day=MONDAY))[2])
