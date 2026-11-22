"""A single captioned demo recording and teacher-reviewed coursework intake."""
from datetime import timedelta
from os import getenv
from pathlib import Path
import re

from . import coursework
from .clock import now
from .domain import DomainError, timestamp
from .event_tickets import DEMO
from .schedule import local_value
from .ui import back, button, e, empty, form, input_field, link, page, panel, select_field, textarea

PATH = "/staff/lecture-studio"
RECORDING = PATH + "/recording"
TITLE = "Локальное время расписания"
CLIP = Path(__file__).with_name("data") / "lecture-studio-demo.mp4"
TRANSCRIPT = (
    (0, "Локальное время", "Локальное время зависит от часового пояса. "
     "Пара в 09:00 в Москве начинается в 06:00 UTC."),
    (8, "Хранение даты", "В базе сохраняем один момент в UTC. "
     "В кабинете показываем часы выбранного календаря."),
    (16, "Сверка недели", "Перед импортом проверяем неделю и локальные часы. "
     "Одинаковые предметы в разное время остаются разными парами."),
)
ASSIGNMENT = "Время в расписании: практика"
PROPOSAL = (
    "1. Занятие начинается в 09:00 по Москве. Запиши этот момент в UTC и объясни пересчёт.\n"
    "2. Один предмет проходит в понедельник в 09:00 и в среду в 14:00. "
    "Укажи, какие поля позволяют сохранить обе пары при повторном импорте."
)


def available(user):
    return getenv("DEAN_EXT_LECTURE_STUDIO") == "1" and user["role"] == "staff" and user["university_id"] == DEMO


def require_demo(user):
    if not available(user):
        raise DomainError(404, "Lecture Studio недоступна.")


def index(request, db):
    require_demo(request.user)
    request.allow_lecture_media = True
    segment = request.query("at", "0")
    if segment not in {str(item[0]) for item in TRANSCRIPT}:
        segment = "0"
    player = (f'<video id="lecture-recording" class="lecture-recording" src="{RECORDING}#t={segment}" '
              f'width="960" height="540" controls playsinline preload="metadata" aria-label="{TITLE}">'
              f'{link(RECORDING, "Открыть запись")}</video>')
    body = back("/staff/work", "К учебным работам")
    body += panel(TITLE, player + "<p>Учебная видеозапись с текстом в кадре. Продолжительность 24 секунды.</p>")
    items = "".join(f'<li><div>{link(f"{PATH}?at={second}#lecture-recording", f"00:{second:02d} · {topic}")}'
                    f'<p>{e(text)}</p></div></li>' for second, topic, text in TRANSCRIPT)
    body += panel("Транскрипт записи", '<p>Текст перенесён из сценария демонстрационной записи.</p>'
                  f'<ol class="rows">{items}</ol>')
    body += panel("Демонстрационный конспект",
                  "<p>Время сохраняется как момент UTC и отображается в часовом поясе календаря. "
                  "День и локальные часы нужно сверять при импорте расписания.</p>"
                  "<p>Темы: часовые пояса, хранение дат, проверка недели и повторяющиеся пары.</p>")
    scopes = coursework.teacher_scopes(db, request.user)
    if not scopes:
        body += panel("Задания по записи", empty("Создание работы доступно после назначения группы и предмета."))
    else:
        choices = [("|".join((item["group_id"], item["period_id"], item["subject_id"])),
                    f'{item["group_name"]} · {item["subject"]}') for item in scopes]
        example_due = local_value(timestamp(now() + timedelta(days=7)), request.user["timezone"])
        fields = select_field("scope", "Группа и предмет", choices)
        fields += input_field("title", "Название", ASSIGNMENT, 'maxlength="120" required')
        fields += textarea("description", "Условия", 2000, required=True, value=PROPOSAL)
        fields += input_field("due", "Срок", example_due.strftime("%Y-%m-%dT%H:%M"), "required", "datetime-local")
        fields += textarea("rubric", "Критерии", 1000, required=True, value="Пересчёт времени | 5\nСверка повторов | 5")
        fields += '<label class="check"><input type="checkbox" name="reviewed" value="1" required>Проверил условия</label>'
        body += panel("Демонстрационные предложения заданий",
                      "<p>Сверь формулировки и выбери группу. Публикация выполняется в учебных работах.</p>"
                      + form(request, PATH, fields + button("Сохранить черновик работы"), command=True))
    return page(request, "Lecture Studio", body, "Демонстрационный материал")


def draft(db, user, fields, key):
    require_demo(user)
    if fields.get("reviewed") != "1":
        raise DomainError(422, "Подтверди проверку задания преподавателем.")
    values = {**fields, "response_mode": "text", "attempt_limit": "2"}
    return coursework.save_assignment(db, user, values, key)


def recording(request, db):
    require_demo(request.user)
    content = CLIP.read_bytes()
    return media_range(content,"video/mp4",request.env.get("HTTP_RANGE",""))


class MediaBody:
    """Serve an immutable local media file in bounded chunks."""
    def __init__(self, path, start, end):
        self.path, self.start, self.end = path, start, end

    def __len__(self):
        return self.end - self.start + 1

    def __iter__(self):
        with self.path.open("rb") as source:
            source.seek(self.start)
            remaining = len(self)
            while remaining and (block := source.read(min(remaining, 256 * 1024))):
                remaining -= len(block)
                yield block


def range_bounds(size, raw):
    if not raw:
        return (0, size - 1)
    match = re.fullmatch(r"bytes=([0-9]{0,10})-([0-9]{0,10})", raw)
    if match and any(match.groups()):
        first, last = match.groups()
        start = int(first) if first else max(0, size - int(last))
        end = min(size - 1, int(last)) if first and last else size - 1
        if start <= end:
            return start, end
    return None


def media_range(content, mime, raw):
    size = len(content)
    headers = [("Content-Type", mime), ("Accept-Ranges", "bytes")]
    bounds = range_bounds(size, raw)
    if bounds is None:
        return 416, b"", headers + [("Content-Range", f"bytes */{size}")]
    if not raw:
        return 200, content, headers
    start, end = bounds
    return 206, content[start:end+1], headers + [("Content-Range", f"bytes {start}-{end}/{size}")]
