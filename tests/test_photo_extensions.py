from base64 import b64decode
from contextlib import closing
from datetime import date, timedelta
import json
import shutil
import struct
import subprocess
from unittest import skipUnless
from unittest.mock import patch
from uuid import uuid4
from wsgiref.validate import validator
import zlib

from dean import ai, attachment, campus_photo, photo_import
from dean.db import connect
from dean.domain import DomainError
from dean.web import create_app

from .test_assisted_dean import Model, PHOTO, PIXEL, upload
from .test_dean import DemoFixture

MONDAY = date(2030, 9, 2)
TITLE = "Синтетическая картография"
JPEG = b64decode(
    "/9j/4AAQSkZJRgABAQAASABIAAD/4QBMRXhpZgAATU0AKgAAAAgAAYdpAAQAAAABAAAAGgAAAAAAA6ABAAMAAAABAAEAAKACAAQAAAABAAAACC"
    "ADAAQAAAABAAAACAAAAAD/7QA4UGhvdG9zaG9wIDMuMAA4QklNBAQAAAAAAAA4QklNBCUAAAAAABDUHYzZjwCyBOmACZjs+EJ+/8AAEQgACAAI"
    "AwEiAAIRAQMRAf/EAB8AAAEFAQEBAQEBAAAAAAAAAAABAgMEBQYHCAkKC//EALUQAAIBAwMCBAMFBQQEAAABfQECAwAEEQUSITFBBhNRYQcicR"
    "QygZGhCCNCscEVUtHwJDNicoIJChYXGBkaJSYnKCkqNDU2Nzg5OkNERUZHSElKU1RVVldYWVpjZGVmZ2hpanN0dXZ3eHl6g4SFhoeIiYqSk5SVl"
    "peYmZqio6Slpqeoqaqys7S1tre4ubrCw8TFxsfIycrS09TV1tfY2drh4uPk5ebn6Onq8fLz9PX29/j5+v/EAB8BAAMBAQEBAQEBAQEAAAAAAAAB"
    "AgMEBQYHCAkKC//EALURAAIBAgQEAwQHBQQEAAECdwABAgMRBAUhMQYSQVEHYXETIjKBCBRCkaGxwQkjM1LwFWJy0QoWJDThJfEXGBkaJicoKS"
    "o1Njc4OTpDREVGR0hJSlNUVVZXWFlaY2RlZmdoaWpzdHV2d3h5eoKDhIWGh4iJipKTlJWWl5iZmqKjpKWmp6ipqrKztLW2t7i5usLDxMXGx8"
    "jJytLT1NXW19jZ2uLj5OXm5+jp6vLz9PX29/j5+v/bAEMAAgICAgICAwICAwUDAwMFBgUFBQUGCAYGBgYGCAoICAgICAgKCgoKCgoKCgwMDA"
    "wMDA4ODg4ODw8PDw8PDw8PD//bAEMBAgICBAQEBwQEBxALCQsQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQE"
    "BAQEBAQEP/dAAQAAf/aAAwDAQACEQMRAD8A9gooor/L89g//9k="
)


class Provider(Model):
    def complete(self, *args, **kwargs):
        value = super().complete(*args, **kwargs)
        if isinstance(value, DomainError):
            raise value
        return value


def lesson(**patches):
    return {"day": 1, "start": "09:00", "end": "10:30", "subject": TITLE, "kind": "семинар",
            "room": "1012", "uncertain": False, **patches}


class PhotoExtensionsTest(DemoFixture):
    def setUp(self):
        super().setUp()
        flags = patch.dict("os.environ", {"DEAN_EXT_PHOTO_IMPORT": "1"})
        flags.start()
        self.addCleanup(flags.stop)

    def model(self, *responses):
        provider = Provider(*responses)
        self.app = create_app(self.path, ai_engine=provider)
        return provider

    def photo(self, student, content=PIXEL, week=MONDAY, form=None, **params):
        return upload(student, PHOTO, {**(form or student.fields(PHOTO, PHOTO)), "week": str(week)}, content, **params)

    def draft(self, response):
        self.assertEqual(response[0], 303, response[2])
        return response[1]["Location"]

    def confirm(self, student, draft):
        route = draft + "/confirm"
        return student.request(route, student.fields(draft, route))

    def test_local_ocr_keeps_room_label_out_of_subject(self):
        text = "Пн 09:00–10:30 QA Семинар Аудитория: 1014"
        record, = campus_photo.parse_boxes([{"x": 0, "y": 0, "h": 18, "text": text}])["events"]
        self.assertEqual((record["subject"], record["room"], record["kind"], record["uncertain"]),
                         ("QA", "1014", "семинар", True))

    def test_local_ocr_keeps_adjacent_course_number_and_room_separate(self):
        text = "Пн 0 9 : 0 0 — 10 : 30 Основы CAD 2 1014"
        record, = campus_photo.parse_boxes([{"x": 0, "y": 0, "h": 18, "text": text}])["events"]
        self.assertEqual((record["start"], record["end"], record["subject"], record["room"]),
                         ("09:00", "10:30", "Основы CAD 2", "1014"))

    def test_photo_flags_lookalike_subject_without_rewriting_it(self):
        original = "MAPK · Введение"
        _, fields, warnings = photo_import.fields_from_photo(lesson(subject=original), MONDAY, None, True)
        self.assertEqual(fields["subject"], original)
        self.assertTrue(any("Сверь алфавит" in warning for warning in warnings))
        _, _, clear = photo_import.fields_from_photo(lesson(subject="Основы CAD"), MONDAY, None, True)
        self.assertEqual(clear, [])

    @skipUnless(shutil.which("ffmpeg"), "requires FFmpeg for cropping")
    @patch.dict("os.environ", {"DEAN_CAMPUS_LAB": "1", "DEAN_PRODUCT": "1"})
    def test_two_weeks_split_before_model_and_imported_for_chosen_date(self):
        left_color = bytes([220, 43, 32])
        right_color = bytes([30, 76, 217])
        pixels = (b"\x00" + left_color * 40 + right_color * 40) * 24
        content = (attachment.PNG + attachment.png_chunk(b"IHDR", struct.pack(">IIBBBBB", 80, 24, 8, 2, 0, 0, 0))
                   + attachment.png_chunk(b"IDAT", zlib.compress(pixels)) + attachment.png_chunk(b"IEND", b""))
        odd = "Нечётная · история"
        even = "Чётная · черчение"
        provider = self.model({"multiple_weeks": True}, {"events": [lesson(subject=odd)]},
                              {"events": [lesson(subject=even)]}, {"events": [lesson(subject=even)]})
        student, other = self.client("student-a"), self.client("student-b")
        whole = self.photo(student, content, form={**student.fields(PHOTO, PHOTO), "cloud": "1"})
        self.assertEqual(whole[0], 422)
        self.assertIn("На снимке две недели", whole[2])
        self.assertIn('name="photo_region"', whole[2])
        self.assertEqual(self.db_one("SELECT count(*) FROM calendar_import"), 0)

        def import_region(side, week):
            return self.draft(self.photo(student, content, week=week, form={
                **student.fields(PHOTO, PHOTO), "cloud": "1", "photo_region": side,
            }))

        left = import_region("left", MONDAY + timedelta(days=2))
        self.assertEqual(left, import_region("left", MONDAY))
        self.assertNotEqual(left, import_region("right", MONDAY))
        next_week = MONDAY + timedelta(days=7)
        right = import_region("right", next_week + timedelta(days=3))

        for index, expected in ((1, left_color), (2, right_color), (3, right_color)):
            args, _, model = provider.calls[index]
            self.assertEqual(model, ai.PHOTO_MODEL)
            payload = args[1]["content"][1]["image_url"]["url"]
            self.assertTrue(payload.startswith("data:image/jpeg;"))
            decoded = subprocess.run([
                "ffmpeg", "-nostdin", "-loglevel", "error", "-i", "pipe:0", "-frames:v", "1",
                "-f", "rawvideo", "-pix_fmt", "rgb24", "pipe:1",
            ], input=b64decode(payload.split(",", 1)[1]), capture_output=True, check=True, timeout=8).stdout
            self.assertEqual(len(decoded), 40 * 24 * 3)
            self.assertTrue(all(abs(a - b) < 8 for a, b in zip(decoded[:3], expected)))

        self.assertIn(odd, student.request(left)[2])
        self.assertNotIn(even, student.request(left)[2])
        self.assertIn(even, student.request(right)[2])
        self.assertEqual(other.request(right)[0], 404)
        self.assertEqual(self.confirm(student, right)[0], 303)
        self.assertEqual(self.db_one("SELECT starts_at FROM personal_event WHERE title=?", (even,)),
                         f"{next_week}T06:00:00+00:00")

    def test_png_date_exact_repeat_other_week_and_account(self):
        recognized = {"events": [lesson(), lesson(), lesson(day=3, start="11:00", end="12:00")]}
        provider = self.model(recognized, recognized, recognized)
        a, b = self.client("student-a"), self.client("student-b")
        same = a.fields(PHOTO, PHOTO)
        preview = self.draft(self.photo(a, week=MONDAY + timedelta(days=3), form=same))
        self.assertEqual(preview, self.draft(self.photo(a, form=same)))
        self.assertEqual(preview, self.draft(self.photo(a)))
        self.assertEqual(len(provider.calls), 1)
        self.assertTrue(provider.calls[0][0][1]["content"][1]["image_url"]["url"].startswith("data:image/png;"))
        self.assertIn("Неделя 02.09.2030 — 08.09.2030", a.request(preview)[2])
        self.assertIn("К импорту 2 из 2", a.request(preview)[2])
        self.assertEqual(b.request(preview)[0], 404)
        self.assertEqual(self.db_one("SELECT count(*) FROM personal_event WHERE title=?", (TITLE,)), 0)
        original_form = a.fields(preview, preview + "/confirm")
        self.assertEqual(a.request(preview + "/confirm", original_form)[0], 303)
        self.assertEqual(a.request(preview + "/confirm", original_form)[0], 303)
        self.assertEqual(self.db_one("SELECT count(*) FROM personal_event WHERE title=?", (TITLE,)), 2)
        a.app = validator(create_app(self.path, ai_engine=provider))
        repeated = self.draft(self.photo(a))
        self.assertEqual(preview + "?repeat=1", repeated)
        self.assertIn("Повторный импорт не изменил календарь", a.request(repeated)[2])
        self.assertIn("Открыть в календаре", a.request(preview)[2])
        self.assertIn(TITLE, a.request(f"/schedule?view=week&date={MONDAY}")[2])
        self.assertNotIn(TITLE, b.request(f"/schedule?view=week&date={MONDAY}")[2])
        self.assertNotEqual(preview, self.draft(self.photo(b)))
        next_week = MONDAY + timedelta(days=7)
        second = self.draft(self.photo(a, week=next_week))
        self.assertEqual(self.confirm(a, second)[0], 303)
        self.assertEqual(self.db_one("SELECT count(*) FROM personal_event WHERE title=?", (TITLE,)), 4)
        self.assertEqual(len(provider.calls), 3)

    def test_uploaded_image_is_required_and_sample_endpoint_is_gone(self):
        provider = self.model({"events": [lesson(day=5)]})
        student = self.client("student-a")
        fields = student.fields(PHOTO, PHOTO)
        fields["week"] = str(MONDAY)
        self.assertEqual(student.request(PHOTO + "/sample", fields)[0], 404)
        self.assertEqual(student.request(PHOTO, fields)[0], 422)
        self.assertEqual(self.db_one("SELECT count(*) FROM calendar_import"), 0)
        self.assertFalse(provider.calls)
        preview = self.draft(self.photo(student, JPEG))
        image_url = provider.calls[0][0][1]["content"][1]["image_url"]["url"]
        self.assertEqual(b64decode(image_url.split(",", 1)[1]), photo_import.image(JPEG)[1])
        self.assertIn("Неделя 02.09.2030", student.request(preview)[2])
        self.assertEqual(self.client("student-b").request(preview)[0], 404)
        self.assertEqual(self.confirm(student, preview)[0], 303)
        friday = MONDAY + timedelta(days=4)
        self.assertIn(f'href="/schedule?date={friday}&amp;view=day&amp;',
                      student.request(preview)[2])
        self.assertIn(TITLE, student.request(f"/schedule?view=week&date={MONDAY}")[2])
        repeat = self.draft(self.photo(student, JPEG))
        self.assertEqual(repeat, preview + "?repeat=1")
        self.assertIn("Этот снимок уже был импортирован", student.request(repeat)[2])
        self.assertEqual(len(provider.calls), 1)

    def test_campus_tries_qwen_then_uses_local_ocr_only_if_model_is_unavailable(self):
        with patch.dict("os.environ", {"DEAN_CAMPUS_LAB": "1", "DEAN_PRODUCT": "1"}), patch(
            "dean.campus_photo.local", return_value=({"events": [lesson(subject="Локальная пара")]}, "Локально"),
        ) as local:
            provider = self.model(DomainError(503, "Провайдер недоступен."), {"events": [lesson()]})
            provider.vision_label = "Qwen через RouterAI"
            student, other = self.client("student-a"), self.client("student-b")
            page = student.request(PHOTO)[2]
            self.assertEqual(page.count(">Распознать расписание по фото</button>"), 1)
            self.assertNotIn("Распознать учебный PNG", page)
            self.assertIn("Qwen через RouterAI", page)
            start = self.draft(self.photo(student))
            self.assertIn("Локальная пара", student.request(start)[2])
            self.assertEqual(len(provider.calls), 1)
            model_draft = self.draft(self.photo(student))
            self.assertNotEqual(model_draft, start)
            self.assertIn(TITLE, student.request(model_draft)[2])
            self.assertEqual(other.request(model_draft)[0], 404)
            self.assertEqual(self.draft(self.photo(student)), model_draft)
            self.assertEqual(len(provider.calls), 2)
            self.assertEqual(provider.calls[0][2], ai.PHOTO_MODEL)
            local.assert_called_once_with(photo_import.image(PIXEL)[1])
            schedule = f"/schedule?view=month&date={MONDAY}"
            before = student.request(schedule)[2]
            self.assertIn(f'{PHOTO}?week={MONDAY}', before)
            self.assertIn('class="dean-schedule-month-cell" data-day-tone="empty"', before)
            semester = student.request(f"/schedule?view=month&date={self.today}")[2]
            self.assertIn('class="dean-schedule-month-cell" data-day-tone="lessons"', semester)
            self.assertEqual(self.confirm(student, model_draft)[0], 303)
            after = student.request(schedule)[2]
            self.assertIn('class="dean-schedule-month-cell" data-day-tone="events"', after)
            day = f'/schedule?view=day&date={MONDAY}'
            self.assertIn(TITLE, student.request(day)[2])
            self.assertNotIn(TITLE, other.request(day)[2])

    def test_jpeg_alt_format_no_duplicate_and_provider_validation_errors(self):
        provider = self.model({"events": []}, DomainError(503, "Провайдер временно недоступен."),
                              {"events": [lesson()]}, {"events": [lesson(kind="лекция", room="1012а")]})
        a = self.client("student-a")
        invalid = self.photo(a, b"bad image")
        self.assertEqual(invalid[0], 422)
        self.assertIn("JPEG или PNG", invalid[2])
        too_large = self.photo(a, b"\0" * (photo_import.MAX_BYTES + 1))
        self.assertEqual(too_large[0], 413)
        huge_png = attachment.PNG + attachment.png_chunk(b"IHDR", struct.pack(">IIBBBBB", 5000, 5000, 8, 2, 0, 0, 0))
        self.assertEqual(self.photo(a, huge_png)[0], 422)
        self.assertFalse(provider.calls)
        unreadable = self.photo(a)
        self.assertEqual(unreadable[0], 422)
        self.assertIn("более чёткий снимок", unreadable[2])
        down = self.photo(a)
        self.assertEqual(down[0], 503)
        self.assertIn("Провайдер временно недоступен", down[2])
        self.assertEqual(self.db_one("SELECT count(*) FROM calendar_import"), 0)
        first = self.draft(self.photo(a))
        self.assertEqual(self.confirm(a, first)[0], 303)
        second = self.draft(self.photo(a, JPEG, filename="расписание.jpg"))
        self.assertNotEqual(first, second)
        self.assertIn("Такая пара уже есть в календаре", a.request(second)[2])
        self.assertEqual(self.confirm(a, second)[0], 303)
        self.assertEqual(self.db_one("SELECT count(*) FROM personal_event WHERE title=?", (TITLE,)), 1)
        image = provider.calls[-1][0][1]["content"][1]["image_url"]["url"]
        self.assertTrue(image.startswith("data:image/jpeg;"))
        self.assertNotIn(b"Exif", b64decode(image.split(",", 1)[1]))

    def test_wrong_reported_week_ambiguous_fields_and_row_edit(self):
        older = MONDAY - timedelta(days=7)
        self.model({"week_start": str(older), "events": [
            lesson(day=None, date=str(older), start="", room=""), lesson(day=3),
        ]})
        a = self.client("student-a")
        preview = self.draft(self.photo(a))
        html = a.request(preview)[2]
        self.assertIn("В заголовке снимка другая неделя", html)
        self.assertIn("Укажи дату пары", html)
        self.assertNotIn("Подтвердить личный импорт", html)
        group = a.fields(preview, preview + "/row")
        row = {"line": "1", "subject": TITLE + " · сверено", "kind": "Практика", "room": "2018а",
               "start": f"{older}T15:00", "end": f"{older}T16:00", "reviewed": "1"}
        self.assertEqual(a.request(preview + "/row", {**group, **row})[0], 303)
        self.assertIn("в один день выбранной недели", a.request(preview)[2])
        payload = a.fields(preview, preview + "/row")
        good_row = {**row, "start": f"{MONDAY}T15:00", "end": f"{MONDAY}T16:00"}
        self.assertEqual(a.request(preview + "/row", {**payload, **good_row, "request_id": uuid4().hex})[0], 303)
        exclude = a.fields(preview, preview + "/exclude")
        self.assertEqual(a.request(preview + "/exclude", {**exclude, "line": "2", "excluded": "1"})[0], 303)
        self.assertEqual(self.confirm(a, preview)[0], 303)
        self.assertIn("выбрано строк: 1 из 2", a.request(preview)[2])
        with closing(connect(self.path)) as db:
            items = db.execute("SELECT * FROM personal_event WHERE title LIKE ?", (TITLE + "%",)).fetchall()
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["title"], TITLE + " · сверено")
        self.assertEqual(items[0]["location"], "2018а")
        self.assertEqual(items[0]["description"], "Импорт из фото · Практика")
        self.assertEqual(items[0]["starts_at"], f"{MONDAY}T12:00:00+00:00")
        self.assertEqual(self.db_one("SELECT count(*) FROM photo_import_context"), 1)
