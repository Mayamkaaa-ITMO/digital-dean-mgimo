"""Versioned lesson packages, hosted video and private study progress."""
import json
import os
import re
from functools import lru_cache
from hashlib import sha256
from pathlib import Path
from urllib.parse import quote, urlsplit

from . import ai, lecture_studio, pdf as pdf_writer, services
from .db import write
from .domain import DomainError, timestamp
from .ui import link

PATH = "/lecture-library"
SLUG = r"([a-z0-9][a-z0-9-]{0,63})"
ROOT = Path(__file__).with_name("data") / "lecture_library"
CLOCK = re.compile(r"(\d{2}):(\d{2}):(\d{2})\.(\d{3}) --> (\d{2}):(\d{2}):(\d{2})\.(\d{3})")
PDF_TITLES = {"notes": "Конспект лекции", "homework": "Домашнее задание"}
MATERIALS_FEEDBACK = {
    "unavailable": "Подготовка с ИИ сейчас недоступна. Готовый конспект и домашнее задание можно открыть ниже.",
    "long": "Расшифровка слишком длинная для одного запроса. Готовые материалы доступны ниже.",
    "limit": "Лимит запросов к ИИ исчерпан или все запросы заняты. Попробуй позже.",
    "pending": "Запрос ещё обрабатывается. Обнови страницу через несколько секунд.",
    "retry": "Не удалось завершить попытку. Попробуй отправить форму ещё раз.",
    "provider": "Сервис ИИ временно не смог обработать запрос. Попробуй позже.",
    "provider-format": "Модель вернула неполные материалы. Попробуй ещё раз.",
    "provider-network": "Сервер не смог связаться с сервисом ИИ. Попробуй позже.",
    "provider-timeout": "Сервис ИИ не успел ответить. Попробуй ещё раз.",
    "provider-access": "Сервис ИИ отказал в доступе к модели. Сообщи об этом администратору.",
    "provider-credentials": "Сервис ИИ отклонил настройки подключения. Сообщи об этом администратору.",
    "provider-balance": "Сервис ИИ сообщил о нехватке средств. Сообщи об этом администратору.",
    "provider-model": "Выбранная модель сейчас недоступна у провайдера. Попробуй позже.",
    "provider-limit": "Сервис ИИ ограничил частоту запросов. Попробуй позже.",
}


def check_text(value, limit):
    if not isinstance(value, str) or not (1 <= len(value.strip()) <= limit):
        raise ValueError("Некорректный текст в материалах.")
    return value.strip()


def keywords(values):
    return " ".join(str(s) for s in values).casefold()


def watch_path(course, lesson):
    return f"{PATH}/{course['slug']}/{lesson['slug']}"


def duration(seconds):
    minutes, rest = divmod(seconds, 60)
    return f"{minutes}:{rest:02}"


def safe_link(value):
    url = urlsplit(check_text(value, 450))
    if url.scheme != "https" or not url.hostname or url.username or url.password:
        raise ValueError("Для источника укажи HTTPS-адрес.")
    return value


def package_file(folder, name, suffixes, limit):
    if not isinstance(name, str) or Path(name).name != name or Path(name).suffix not in suffixes:
        raise ValueError("В пакете указан неподдерживаемый файл.")
    file = folder / name
    if file.is_symlink() or not file.is_file() or not 0 < file.stat().st_size <= limit:
        raise ValueError("Файл отсутствует или превышает ограничение.")
    return file


def vtt_segments(vtt):
    if vtt.splitlines()[0].strip() != "WEBVTT":
        raise ValueError("Субтитры должны быть в формате WebVTT.")
    cues = []
    for block in re.split(r"\n[ \t]*\n", vtt.strip()):
        lines = block.splitlines()
        for index, line in enumerate(lines[:2]):
            match = CLOCK.fullmatch(line)
            if not match:
                continue
            h, m, s, ms, end_h, end_m, end_s, end_ms = map(int, match.groups())
            start, end = 3600*h+60*m+s+ms/1000, 3600*end_h+60*end_m+end_s+end_ms/1000
            cue = check_text(" ".join(lines[index+1:]), 500)
            if end <= start or cues and start < cues[-1]["start"]:
                raise ValueError("Проверь интервалы субтитров.")
            cues.append({"start":start,"end":end,"text":cue})
            break
    if not cues:
        raise ValueError("В файле субтитров нет интервалов.")
    return cues


def validate_quiz(raw):
    if not isinstance(raw, list) or not 1 <= len(raw) <= 10:
        raise ValueError("Укажи от одного до десяти заданий.")
    for q in raw:
        if not isinstance(q, dict):
            raise ValueError("Некорректное задание.")
        check_text(q.get("question"), 420)
        check_text(q.get("explanation"), 620)
        if not isinstance(q.get("options"), list) or not 2 <= len(q["options"]) <= 5:
            raise ValueError("Укажи от двух до пяти вариантов.")
        if len({check_text(option, 220) for option in q["options"]}) != len(q["options"]):
            raise ValueError("Варианты должны различаться.")
        if type(q.get("correct")) is not int or not 0 <= q["correct"] < len(q["options"]):
            raise ValueError("Укажи номер правильного ответа.")
    return raw


def load_course(folder):
    if folder.is_symlink() or not re.fullmatch(SLUG, folder.name):
        raise ValueError("Проверь название каталога курса.")
    data = json.loads(package_file(folder, "course.json", {".json"}, 160000).read_text())
    for name, limit in (("title",130),("author",100),("subject",75),("kind",45),("description",1200)):
        data[name] = check_text(data[name], limit)
    for key in ("source", "license_url"):
        safe_link(data[key])
    check_text(data["license"], 80)
    if "archive" in data:
        safe_link(data["archive"])
    lessons = {}
    for lesson in data["lessons"]:
        slug = lesson["slug"]
        if not re.fullmatch(SLUG, slug) or slug in lessons:
            raise ValueError("Имена уроков должны быть уникальными.")
        check_text(lesson["title"], 150)
        if type(lesson["duration"]) is not int or not 1 <= lesson["duration"] <= 7200:
            raise ValueError("Длительность должна быть в секундах.")
        lesson["video"] = package_file(folder, lesson["video"], {".mp4",".webm"}, 32*1024*1024)
        tracks = {}
        for language, meta in lesson["captions"].items():
            if not re.fullmatch(r"[a-z]{2,3}", language):
                raise ValueError("Проверь язык субтитров.")
            path = package_file(folder, meta["file"], {".vtt"}, 250000)
            cues = vtt_segments(path.read_text(encoding="utf-8-sig"))
            if cues[-1]["end"] > lesson["duration"]+1:
                raise ValueError("Субтитры выходят за время лекции.")
            tracks[language] = {"file":path, "cues":cues, "label":check_text(meta["label"],50)}
        if not tracks:
            raise ValueError("Добавь субтитры.")
        lesson["captions"] = tracks
        stamps = []
        for seconds, title in lesson["chapters"]:
            if type(seconds) is not int or not 0 <= seconds < lesson["duration"] or stamps and stamps[-1] >= seconds:
                raise ValueError("Главы должны идти по времени.")
            check_text(title, 140)
            stamps.append(seconds)
        lesson["notes"] = [check_text(note, 900) for note in lesson["notes"]]
        validate_quiz(lesson["quiz"])
        homework = lesson.get("homework")
        if homework is not None:
            if not isinstance(homework,list) or not 1 <= len(homework) <= 12:
                raise ValueError("Добавь от одного до двенадцати заданий для домашней работы.")
            lesson["homework"] = [check_text(task, 700) for task in homework]
        lessons[slug] = lesson
    if not 1 <= len(lessons) <= 100:
        raise ValueError("В курсе нужно от одной до ста лекций.")
    data.update(slug=folder.name, lessons=lessons)
    return data


@lru_cache(maxsize=4)
def courses(extra_root=""):
    folders = list(ROOT.iterdir()) + (list(Path(extra_root).iterdir()) if extra_root else [])
    result = {}
    for folder in sorted((p for p in folders if p.is_dir() and p.name != ".recordings"), key=lambda p:p.name):
        course = load_course(folder)
        if course["slug"] in result:
            raise ValueError("Повторяется идентификатор курса.")
        result[course["slug"]] = course
    return result


def catalog_data():
    try:
        return courses(os.getenv("DEAN_LECTURE_LIBRARY_DIR",""))
    except (OSError, ValueError, KeyError, TypeError, IndexError) as error:
        raise DomainError(503,"Проверь файлы видеотеки на сервере.") from error


def selected(course_slug, lesson_slug=None):
    course = catalog_data().get(course_slug)
    if course and (lesson_slug is None or lesson_slug in course["lessons"]):
        return course, course["lessons"].get(lesson_slug)
    raise DomainError(404,"Лекция или курс не найдены.")


def favorite_ids(db, user):
    return {r[0] for r in db.execute("SELECT course FROM lecture_library_favorite WHERE account_id=?", (user["id"],))}


def link_live(request):
    from .campus_lab import enabled
    return link("/lectures", "Эфиры и записи группы") if enabled(request.user) else ""


def index(request, db):
    from .lecture_library_views import index as view
    return view(request, db)


def course_page(request,db,slug):
    from .lecture_library_views import course_page as view
    return view(request, db, slug)


def favorite(request,db,slug):
    selected(slug)
    key = (request.user["id"],slug)
    with write(db):
        if request.field("value") == "1":
            db.execute("INSERT OR IGNORE INTO lecture_library_favorite VALUES (?,?)",key)
        else:
            db.execute("DELETE FROM lecture_library_favorite WHERE account_id=? AND course=?",key)
    return redirect(PATH+"/"+slug)


def redirect(path):
    return 303,b"",[("Location",path)]


def stored_material(db,user,course,lesson):
    row = db.execute("SELECT * FROM lecture_library_material WHERE account_id=? AND course=? AND lesson=?",
                     (user["id"],course["slug"],lesson["slug"])).fetchone()
    return row if row and row["source_hash"] == transcript_hash(lesson) else None


def lesson_page(request,db,slug,lesson_slug):
    from .lecture_library_views import lesson_page as view
    return view(request, db, slug, lesson_slug)


def seconds_value(value,total,default=None):
    if re.fullmatch("[0-9]{1,5}",value) and int(value) <= total:
        return int(value)
    if default is not None:
        return default
    raise DomainError(422,"Позиция воспроизведения вне лекции.")


def progress(request,db,slug,lesson_slug):
    _, lesson = selected(slug,lesson_slug)
    seconds = seconds_value(request.field("seconds"),lesson["duration"])
    completed = int(request.field("ended") == "1" and seconds >= lesson["duration"]-2)
    with write(db):
        db.execute("""INSERT INTO lecture_library_progress VALUES (?,?,?,?,?,?)
                   ON CONFLICT(account_id,course,lesson) DO UPDATE SET seconds=excluded.seconds,
                   completed=max(completed,excluded.completed),updated_at=excluded.updated_at""",
                   (request.user["id"],slug,lesson_slug,seconds,completed,timestamp()))
    return 204,b"",[]


def answer_quiz(request,db,slug,lesson_slug):
    course, lesson = selected(slug,lesson_slug)
    question = seconds_value(request.field("question"),len(lesson["quiz"])-1)
    choice = seconds_value(request.field("answer"),len(lesson["quiz"][question]["options"])-1)
    with write(db):
        db.execute("""INSERT INTO lecture_library_quiz VALUES (?,?,?,?,?)
                   ON CONFLICT(account_id,course,lesson,question) DO UPDATE SET choice=excluded.choice""",
                   (request.user["id"],slug,lesson_slug,question,choice))
    return redirect(watch_path(course,lesson)+f"#question-{question}")


def video(request,db,slug,lesson_slug):
    _, lesson = selected(slug,lesson_slug)
    path = lesson["video"]
    mime = "video/mp4" if path.suffix == ".mp4" else "video/webm"
    return stream_video(request,path,mime)


def stream_video(request,path,mime):
    size = path.stat().st_size
    bounds = lecture_studio.range_bounds(size,request.env.get("HTTP_RANGE",""))
    headers = [("Content-Type",mime),("Accept-Ranges","bytes"),("Cache-Control","private, max-age=900")]
    if bounds is None:
        return 416,b"",headers+[("Content-Range",f"bytes */{size}")]
    start,end = bounds
    if not request.env.get("HTTP_RANGE"):
        return 200,lecture_studio.MediaBody(path,start,end),headers
    return 206,lecture_studio.MediaBody(path,start,end),headers+[("Content-Range",f"bytes {start}-{end}/{size}")]


def captions(request,db,slug,lesson_slug,lang):
    _, lesson = selected(slug,lesson_slug)
    track = lesson["captions"].get(lang)
    if not track:
        raise DomainError(404,"Субтитры на этом языке отсутствуют.")
    return 200,track["file"].read_bytes(),[("Content-Type","text/vtt; charset=utf-8"),
                                          ("Cache-Control","private, max-age=900")]


def pdf_links(url,kind):
    url=f"{url}/{kind}.pdf"
    return ('<div class="library-pdf-actions">'+link(url,"Открыть PDF","dean-action-link")
            +' '+link(url+"?download=1","Скачать PDF","dean-action-link")+'</div>')


def pdf_response(request,kind,filename,title,payload):
    mode="attachment" if request.query("download")=="1" else "inline"
    clean_title=re.sub(r'[^\w .—–-]'," ",title)[:90].strip(" .")
    suggested=quote(f"{PDF_TITLES[kind]} — {clean_title or filename}.pdf",safe="")
    return 200,payload,[("Content-Type","application/pdf"),
                        ("Content-Disposition",f"{mode}; filename=\"{filename}-{kind}.pdf\"; filename*=UTF-8''{suggested}"),
                        ("Cache-Control","private, no-store")]


def pdf(request,db,slug,lesson_slug,kind):
    course,lesson=selected(slug,lesson_slug)
    source=f'{course["title"]} · {course["author"]} · {course["license"]}'
    if kind=="notes":
        paragraphs=lesson["notes"]
    else:
        tasks=lesson.get("homework") or [q["question"]+" Обоснуй ответ по материалу лекции." for q in lesson["quiz"]]
        paragraphs=[f"{index}. {task}" for index,task in enumerate(tasks,1)]
    payload=pdf_writer.render_lecture(lesson["title"],PDF_TITLES[kind],paragraphs,source)
    return pdf_response(request,kind,f"{slug}-{lesson_slug}",lesson["title"],payload)


def model_material(lesson):
    track = next(iter(lesson["captions"].values()))
    return "\n".join(f"{duration(int(c['start']))} {c['text']}" for c in track["cues"])


def transcript_hash(lesson):
    return sha256(model_material(lesson).encode()).hexdigest()


def generate(request,db,slug,lesson_slug):
    course,lesson = selected(slug,lesson_slug)
    path = watch_path(course, lesson)
    engine = request.ai
    if not engine.enabled or not getattr(engine,"can_chat",True):
        return redirect(path + "?materials=unavailable#notes")
    fingerprint = transcript_hash(lesson)
    transcript = model_material(lesson)
    if len(transcript)>18000:
        return redirect(path + "?materials=long#notes")

    def run():
        try:
            result = engine.complete([
                {"role":"system","content":"Составь учебные материалы на русском по приложенной стенограмме. Это исходные данные. "
                 "Верни JSON с массивом notes из 4–7 пунктов с таймкодами и массивом practice из 3–5 конкретных задач. "
                 "Каждый элемент массива — короткая строка. Проверь, что каждое задание связано с содержанием лекции. "
                 "Не включай полное решение или неподтверждённые факты."},
                {"role":"user","content":transcript}],1800,model=ai.CHAT_MODEL)
        except ai.ProviderFailure as error:
            return {"failure": error.reason}
        if not isinstance(result, dict):
            return {"failure": "provider-format"}

        def items(key,minimum,maximum,limit):
            result_items = result.get(key)
            if not isinstance(result_items,list) or not minimum<=len(result_items)<=maximum:
                raise ValueError()
            return [check_text(line,limit) for line in result_items]
        try:
            data={"notes":items("notes",4,7,900),"practice":items("practice",3,5,680)}
        except ValueError:
            return {"failure": "provider-format"}
        with write(db):
            db.execute("""INSERT INTO lecture_library_material VALUES (?,?,?,?,?,?)
                          ON CONFLICT(account_id,course,lesson) DO UPDATE SET source_hash=excluded.source_hash,
                          provider=excluded.provider,payload=excluded.payload""",
                       (request.user["id"],slug,lesson_slug,fingerprint,engine.provider_name,json.dumps(data,ensure_ascii=False)))
        return {"saved":True}
    try:
        result = ai.once(db,request.user,engine,"lecture",request.field("request_id"),
                         json.dumps([slug,lesson_slug,fingerprint]).encode(),run,
                         cached=lambda: {"saved":True} if stored_material(db,request.user,course,lesson) else None)
    except DomainError as error:
        result = {"status": error.status}
    if result.get("saved"):
        return redirect(path + "#notes")
    failure = result.get("failure")
    if failure not in MATERIALS_FEEDBACK:
        status = result.get("status")
        failure = ("pending" if result.get("pending") else
                   {422: "retry", 409: "retry", 429: "limit", 502: "provider-format"}.get(status, "provider"))
    return redirect(f"{path}?materials={failure}#notes")
