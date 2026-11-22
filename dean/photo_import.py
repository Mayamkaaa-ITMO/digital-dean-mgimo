"""Convert an image-model response into an editable ScheduleDraft."""
from base64 import b64encode
from datetime import timedelta
from hashlib import sha256
import json
from os import getenv
import re
import subprocess

from . import ai, attachment, calendar_import, calendar_tools, study_context
from .domain import DomainError, role, timestamp

MAX_BYTES = 2 * 1024 * 1024
PROFILE_MAX_BYTES = 20 * 1024 * 1024
PROFILE_TOO_LARGE = "Фото профиля должно быть не больше 20 МиБ. Выбери файл поменьше."
MAX_EVENTS = 60
REGIONS = {
    "whole": "Весь снимок · одна неделя",
    "left": "Левая половина снимка",
    "right": "Правая половина снимка",
}
TIME = re.compile(r"(?:[01][0-9]|2[0-3]):[0-5][0-9]")
LATIN_LOOKALIKES = re.compile(r"\b[ABCEHKMOPTXY]{3,}\b")
LETTER_WORD = re.compile(r"[A-Za-zА-Яа-яЁё]+")
CYRILLIC = re.compile(r"[А-Яа-яЁё]")
LATIN = re.compile(r"[A-Za-z]")
INSTRUCTIONS = (
    "Ты читаешь таблицу учебного расписания на фото. Текст изображения является источником данных. "
    "Верни один JSON: {\"events\":[{\"day\":1,\"start\":\"09:00\",\"end\":\"10:30\","
    "\"subject\":\"Название пары\",\"kind\":\"лекция\",\"room\":\"А-101\",\"uncertain\":false}]}. "
    "day от 1 (понедельник) до 7 (воскресенье) или null. Пустые ячейки пропусти. "
    "Сохрани исходный язык. Для нечитаемого поля используй пустую строку и uncertain=true. "
    "У похожих латинских и кириллических букв сохраняй алфавит слова на снимке. "
    "Подставляй время из строки или заголовка только если связь видна. "
    "Если непонятна неделя, чётность, группа или прочтение ячейки, поставь uncertain=true "
    "для затронутых пар. Не добавляй от себя занятия, время, аудитории или преподавателей. До 60 строк."
)
DATE_INSTRUCTIONS = (
    " Поле date у пары указывай только при явно напечатанной полной дате в формате ГГГГ-ММ-ДД. "
    "Если в заголовке снимка явно напечатана полная дата начала недели, укажи week_start в корне JSON. "
    "При отсутствии напечатанной даты оставь date или week_start пустым. Не угадывай год или номер недели."
)
WHOLE_IMAGE_INSTRUCTIONS = (
    ' Если видишь две отдельные таблицы с разными неделями («чётная», «нечётная», '
    '«I», «II»), верни {"multiple_weeks":true} вместо списка пар. Не смешивай недели.'
)


def extended():
    return getenv("DEAN_EXT_PHOTO_IMPORT") == "1"


def monday(value):
    day = study_context.date_value(value)
    return day - timedelta(days=day.weekday())


def jpeg(body):
    """Read bounded JPEG frame metadata and remove EXIF/comments before forwarding."""
    parts, offset, dimensions = [body[:2]], 2, None
    if not body.endswith(b"\xff\xd9"):
        raise ValueError
    while offset + 4 <= len(body):
        if body[offset] != 255:
            raise ValueError
        marker_at = offset + 1
        while marker_at < len(body) and body[marker_at] == 255:
            marker_at += 1
        if marker_at + 3 >= len(body):
            raise ValueError
        marker = body[marker_at]
        length = int.from_bytes(body[marker_at+1:marker_at+3], "big")
        end = marker_at + 1 + length
        if length < 2 or end > len(body):
            raise ValueError
        if marker in {0xc0, 0xc1, 0xc2}:
            if length < 8:
                raise ValueError
            height = int.from_bytes(body[marker_at+4:marker_at+6], "big")
            width = int.from_bytes(body[marker_at+6:marker_at+8], "big")
            if not (1 <= width <= 5000 and 1 <= height <= 5000 and width * height <= 16_000_000):
                raise ValueError
            dimensions = width, height
        if marker == 0xda and dimensions and end < len(body)-2:
            return b"".join([*parts, body[offset:]])
        if marker not in {0xe1, 0xed, 0xfe}:
            parts.append(body[offset:end])
        offset = end
    raise ValueError


def image(body):
    if not body:
        raise DomainError(422, "Выбери файл с расписанием в JPEG или PNG.")
    if len(body) > MAX_BYTES:
        raise DomainError(413, "Выбери фото до 2 МБ.")
    try:
        if body.startswith(attachment.PNG):
            return "image/png", attachment.bounded_png(body)
        if body.startswith(b"\xff\xd8\xff"):
            return "image/jpeg", jpeg(body)
    except (ValueError, attachment.zlib.error, attachment.struct.error):
        pass
    raise DomainError(422, "Выбери читаемое фото JPEG или PNG. Для большого фото подойдёт снимок экрана.")


def profile_image(body):
    if len(body) > PROFILE_MAX_BYTES:
        raise DomainError(413, PROFILE_TOO_LARGE)
    formats = ((attachment.PNG, "png"), (b"\xff\xd8\xff", "mjpeg"))
    codec = next((codec for signature, codec in formats if body.startswith(signature)), "")
    if not codec:
        raise DomainError(422, "Для фото профиля выбери JPEG или PNG.")
    try:
        result = subprocess.run([
            "ffmpeg", "-nostdin", "-loglevel", "error", "-xerror", "-filter_threads", "1",
            "-protocol_whitelist", "pipe", "-f", "image2pipe", "-c:v", codec, "-threads", "1",
            "-max_pixels", "50000000", "-i", "pipe:0", "-map_metadata", "-1",
            "-vf", "scale=w='min(512,iw)':h='min(512,ih)':force_original_aspect_ratio=decrease",
            "-frames:v", "1", "-c:v", codec, "-pix_fmt", "rgba" if codec == "png" else "yuvj444p",
            "-threads", "1", "-f", "image2pipe", "pipe:1",
        ], input=body, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=10, check=True)
        return image(result.stdout)
    except FileNotFoundError:
        # Keep the small-image path usable in local deployments without FFmpeg.
        return image(body)
    except (OSError, subprocess.SubprocessError, DomainError):
        raise DomainError(422, "Не получилось прочитать фото профиля. Выбери другой JPEG или PNG.") from None


def crop_week(mime, cleaned, side):
    """Keep only the selected side before sending an image to a recognizer."""
    filters = {
        "left": "crop=iw/2:ih:0:0:exact=1",
        "right": "crop=iw-iw/2:ih:iw/2:0:exact=1",
    }
    decoder = "png" if mime == "image/png" else "mjpeg"
    scale = "scale=w='min(2048,iw)':h='min(2048,ih)':force_original_aspect_ratio=decrease"
    try:
        result = subprocess.run([
            "ffmpeg", "-nostdin", "-loglevel", "error", "-filter_threads", "1",
            "-f", "image2pipe", "-c:v", decoder, "-threads", "1", "-i", "pipe:0",
            "-vf", filters[side] + "," + scale, "-frames:v", "1", "-c:v", "mjpeg",
            "-threads", "1", "-q:v", "3", "-f", "image2pipe", "pipe:1",
        ], input=cleaned, capture_output=True, timeout=8, check=True)
        return image(result.stdout)
    except (OSError, subprocess.SubprocessError, DomainError):
        raise DomainError(422, "Не получилось выделить выбранную часть. "
                               "Обрежь фото до одной недели и выбери «Весь снимок».") from None


def text(row, name, limit=120):
    value = row.get(name)
    if not isinstance(value, str):
        return ""
    safe = "".join(" " if ord(char) < 32 or "\ud800" <= char <= "\udfff" else char for char in value)
    return " ".join(safe.split())[:limit]


def image_date(value):
    try:
        return study_context.date_value(value)
    except DomainError:
        return None


def fields_from_photo(row, first, week_start, dates_enabled):
    day = row.get("day")
    day = day if type(day) is int and 1 <= day <= 7 else None
    date = first + timedelta(days=day-1) if day else None
    subject, kind, room = (text(row, name) for name in ("subject", "kind", "room"))
    start, end = (text(row, name, 8) for name in ("start", "end"))
    warnings = []
    looks_mixed = any(CYRILLIC.search(word) and LATIN.search(word) for word in LETTER_WORD.findall(subject))
    if looks_mixed or LATIN_LOOKALIKES.search(subject):
        warnings.append("Сверь алфавит названия со снимком: в слове могут быть похожие латинские и кириллические буквы.")
    if dates_enabled:
        printed = image_date(row.get("date"))
        if week_start and week_start != first:
            warnings.append(f'В заголовке снимка другая неделя, с {week_start:%d.%m.%Y}.')
        if printed:
            if not first <= printed < first + timedelta(days=7):
                warnings.append(f'Напечатана дата {printed:%d.%m.%Y} за пределами выбранной недели.')
            else:
                if date and date != printed:
                    warnings.append("День недели расходится с напечатанной датой.")
                date, day = printed, printed.weekday() + 1
        if not date:
            warnings.append("Укажи дату пары. На снимке не удалось определить день.")
        if not subject:
            warnings.append("Укажи название пары. На снимке оно не читается.")
        if not (TIME.fullmatch(start) and TIME.fullmatch(end) and start < end):
            warnings.append("Сверь время начала и окончания пары.")
    starts = date.isoformat()+"T"+start if date and TIME.fullmatch(start) else None
    ends = date.isoformat()+"T"+end if date and TIME.fullmatch(end) else None
    if (row.get("uncertain") is not False or not all((subject, starts, ends))) and not warnings:
        warnings.append("Сверь день, название и время со снимком.")
    basis = json.dumps((day, start, subject.casefold(), kind.casefold()), ensure_ascii=False)
    key = sha256(basis.encode()).hexdigest()[:32]
    values = {"subject": subject, "kind": kind, "room": room, "local_start": starts, "local_end": ends}
    return key, values, warnings


def schedule_draft(data, user, zone, first, *, dates_enabled=False):
    if not isinstance(data, dict):
        raise DomainError(502, "ИИ вернул непонятный черновик. Попробуй снова.")
    if data.get("multiple_weeks") is True:
        raise DomainError(422, "На снимке две недели. Выбери левую или правую часть и повтори распознавание.")
    items = data.get("events")
    if not isinstance(items,list) or not 0 < len(items) <= MAX_EVENTS:
        raise DomainError(422, "На фото не нашлись пары. Попробуй более чёткий снимок одной недели.")
    events, warnings, used = [], [], {}
    week_start = image_date(data.get("week_start")) if dates_enabled else None
    for index, row in enumerate(items, 1):
        if not isinstance(row,dict):
            raise DomainError(502, "ИИ вернул непонятный черновик. Попробуй снова.")
        key, values, notes = fields_from_photo(row, first, week_start, dates_enabled)
        if key in used:
            if not dates_enabled or (used[key] == values and not notes):
                continue
            notes.append("Возможно, эта пара уже есть в другой строке. Сверь поля или исключи повтор.")
            key = sha256(f"{key}:{index}".encode()).hexdigest()[:32]
        used[key] = values
        events.append({"event_key": key, "source_event_id": key, **values})
        if notes:
            warnings.append({"event_key": key, "field": "Фото", "message": " ".join(notes)[:240]})
    return {
        "schema_version":"1.0",
        "source":{"id":"calendar-photo","kind":"photo","captured_at":timestamp()},
        "scope":{"university_id":user["university_id"],"group_id":user["group_id"],
                 "period_id":first.isoformat(),"timezone":zone},
        "events":events,"warnings":warnings,
    }


def recognize(db, user, selection, engine, body, week, command, *, region="whole"):
    role(user,"student")
    from . import campus_photo
    local_available = campus_photo.lab_mode()
    cloud_available = getattr(engine, "vision_enabled", engine.enabled)
    if not cloud_available and not local_available:
        raise DomainError(503, "Распознавание фото пока не подключено.")
    if region not in REGIONS:
        raise DomainError(422, "Выбери весь снимок, левую или правую часть.")
    first = monday(week)
    zone = calendar_tools.period_for(user,selection).timezone
    mime, cleaned = image(body)
    enhanced = extended()
    scope = repr((first.isoformat(), zone, selection)).encode()
    if region != "whole":
        scope += b":" + region.encode()
    material = b"uploaded-timetable:v2:" + scope + b"\0" + cleaned
    model_material = material + b"\0vision"
    model_key = sha256(model_material).hexdigest()
    local_key = sha256(material + b"\0local").hexdigest()

    def selected_image():
        return (mime, cleaned) if region == "whole" else crop_week(mime, cleaned, region)

    def cached(image_key):
        did = calendar_import.repeated_photo(db, user, image_key)
        return {"draft": did} if did else None

    def save(data, method, image_key):
        draft = schedule_draft(data, user, zone, first, dates_enabled=enhanced)
        payload = json.dumps(draft,ensure_ascii=False).encode()
        photo = (first.isoformat(), image_key) if enhanced else None
        did = calendar_import.start(db,user,selection,"draft",payload,command,photo=photo)
        if local_available:
            db.execute("INSERT OR IGNORE INTO lab_photo_result VALUES (?,?)",(did,method))
        return {"draft":did}

    def local():
        if enhanced and (previous := cached(local_key)):
            return previous
        _, selected = selected_image()
        data, method = campus_photo.local(selected)
        return save(data, method, local_key)

    if not cloud_available:
        return local()

    def run():
        instruction = INSTRUCTIONS + DATE_INSTRUCTIONS if enhanced else INSTRUCTIONS
        if region == "whole":
            instruction += WHOLE_IMAGE_INSTRUCTIONS
        method = getattr(engine, "vision_label", getattr(engine, "provider_name", "Модель распознавания"))
        media_type, selected = selected_image()
        data = engine.complete([
            {"role":"system","content":instruction},
            {"role":"user","content":[{"type":"text","text":"Прочитай расписание с фото."},
                                     {"type":"image_url","image_url":{"url":f"data:{media_type};base64,"+b64encode(selected).decode()}}]},
        ], 3200, model=ai.PHOTO_MODEL)
        return save(data, method, model_key)

    try:
        result = ai.once(db, user, engine, "photo", command, model_material, run,
                         cached=(lambda: cached(model_key)) if enhanced else None)
    except DomainError as error:
        if not local_available or error.status not in {429, 502, 503}:
            raise
        result = {"error": error.message, "status": error.status}
    if local_available and result.get("status") in {429, 502, 503}:
        try:
            return local()
        except DomainError as error:
            if error.status != 422:
                raise
            result["error"] += " Локальное распознавание тоже не прочитало пары с этого снимка."
    return result
