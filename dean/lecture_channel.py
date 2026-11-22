"""Private group broadcasts. Each playable segment has an ordered, replay-safe receipt."""
from datetime import timedelta
from hashlib import file_digest, sha256
from os import getenv
from pathlib import Path
from contextlib import closing
from threading import Thread, Event
import logging
import sqlite3

from . import ai, campus_lab, coursework, lecture_analysis, lecture_imports, lecture_library, pdf as pdf_writer, services, workspace
from .clock import now
from .db import write, connect
from .domain import DomainError, field, identifier, require, role, timestamp
from .schedule import local_value
from .ui import back, button, e, form, hidden, input_field, link, page, panel, select_field, textarea
import json

PATH = "/lectures"
IMPORT_PATH = PATH+"/import"
SAMPLE = Path(__file__).with_name("data")/"campus-lecture.mp3"
LIMIT = 512*1024*1024
ANALYSIS_LEASE = timedelta(minutes=2)
ANALYSIS_QUEUE_NOTICE = timedelta(minutes=2)
ANALYSIS_ATTEMPTS = 2
ANALYSIS_INTERRUPTED = "Обработка прервалась. Сервер не сохранил причину. Лекция осталась в списке, обработку можно повторить."
LEGACY_INTERRUPTED = "Обработка прервалась. Лекция осталась в списке, обработку можно повторить."
ANALYSIS_STOPPED = "Обработка остановлена. Лекция осталась в списке, обработку можно повторить."
LEGACY_HOMEWORK_START = "1. Объясни своими словами фрагмент «"


class PersonalLecturePending(DomainError):
    def __init__(self, lecture):
        super().__init__(409, "Одновременно можно обрабатывать одну личную лекцию.")
        self.lecture = lecture


def catalogue(request,db):
    user=request.user
    campus_lab.access(user)
    body=back("/lecture-library","К видеотеке")+link(campus_lab.PATH,"К кампусу","dean-action-link")
    if can_import(db,user):
        body+=link(IMPORT_PATH,"Добавить лекцию","dean-action-link")
    if user["role"]=="staff":
        options=[("|".join((r["group_id"],r["period_id"],r["subject_id"])),r["group_name"]+" · "+r["subject"])
                 for r in coursework.teacher_scopes(db,user)]
        if options:
            controls=input_field("title","Название лекции","","required maxlength=120")+select_field("scope","Группа и дисциплина",options)
            body+=panel("Новый эфир",form(request,PATH,controls+button("Открыть эфир для группы"),command=True))
    rows=visible_lectures(db,user)
    items = "".join(
        f'<li><div>{link(PATH + "/" + row["id"], row["title"])}'
        f'<p class="meta">{"Запись" if row["ended"] else "Прямой эфир"}</p></div></li>'
        for row in rows
    )
    return page(request,"Лекции группы",body+panel("Эфиры и записи",'<ul class="rows">'+items+'</ul>' if items else "<p>Пока нет лекций для этой группы.</p>"),section="lecture-library")


def visible_lectures(db,user,*,imports_only=False):
    if user["role"]=="staff":
        condition, args = "owner=?", (user["id"],)
    else:
        condition = "((visibility='group' AND group_id=? AND period_id=?) OR owner=?)"
        args = (user["group_id"],user["period_id"],user["id"])
    if imports_only:
        condition+=" AND (recording_url IS NOT NULL OR recording_mime IS NOT NULL)"
    return db.execute("""SELECT id,title,ended,visibility,transcript IS NOT NULL AS transcript,analysis_status,
                      analysis_until,analysis_error,
                      (SELECT queued FROM lecture_analysis_job WHERE lecture_id=lab_lecture.id) AS queued
                      FROM lab_lecture WHERE university_id=? AND """+condition+" ORDER BY created DESC,id",
                      (user["university_id"],*args)).fetchall()


def visible_imports(db,user):
    return visible_lectures(db,user,imports_only=True) if campus_lab.enabled(user) else []


def subject_lectures(db, user, subject_id, period_id):
    if (user["role"] != "student" or not campus_lab.enabled(user)
            or not subject_id or period_id != user["period_id"]):
        return []
    return db.execute("""
        SELECT id,title,ended,transcript IS NOT NULL AND COALESCE(notes,'')<>'' AS has_notes,
               transcript IS NOT NULL AND COALESCE(homework,'')<>'' AS has_homework
        FROM lab_lecture WHERE university_id=? AND group_id=? AND period_id=? AND subject_id=?
          AND visibility='group' AND (ended IS NULL OR recording_mime IS NOT NULL OR recording_url IS NOT NULL
               OR EXISTS (SELECT 1 FROM lab_segment WHERE lecture=lab_lecture.id))
        ORDER BY created DESC,id LIMIT 6
    """, (user["university_id"], user["group_id"], period_id, subject_id)).fetchall()


def import_options(db,user):
    if user["role"] == "student":
        if not user["group_id"] or not user["period_id"]:
            return {}
        return {"|".join((user["group_id"], user["period_id"], "")): "Только я"}
    options={"|".join((row["group_id"],row["period_id"],row["subject_id"])):
             f'{row["group_name"]} · {row["subject"]}' for row in coursework.teacher_scopes(db,user)}
    group_periods=db.execute("""SELECT DISTINCT s.group_id,a.period_id,g.name,p.label FROM staff_group s
                              JOIN account a ON a.university_id=s.university_id AND a.group_id=s.group_id AND a.role='student'
                              JOIN study_group g ON g.university_id=s.university_id AND g.id=s.group_id
                              JOIN academic_period p ON p.university_id=s.university_id AND p.id=a.period_id
                              WHERE s.staff_id=? AND s.university_id=? ORDER BY g.name,p.starts_on DESC""",
                             (user["id"],user["university_id"]))
    for row in group_periods:
        key="|".join((row["group_id"],row["period_id"],""))
        options[key]=f'{row["name"]} · {row["label"]} · Любая тема'
    return options


def can_import(db,user):
    return bool(campus_lab.enabled(user) and import_options(db,user))


def analysis_options():
    return ('<details class="library-import-options"><summary>Настройки обработки</summary>'
            '<p class="meta">По умолчанию речь распознаётся локальной моделью Whisper.</p>'
            '<label class="check"><input type="checkbox" name="cloud" value="1">Разрешить передачу аудио в OpenRouter, если локальный Whisper недоступен</label>'
            '<label class="check"><input type="checkbox" name="model" value="1">Разрешить передачу расшифровки языковой модели для подготовки материалов</label></details>')


def upload_controls():
    return ('<label>Файл MP4 или WebM<input type="file" name="file" accept=".mp4,.webm,video/mp4,video/webm" required></label>'
            '<p class="meta">До 64 МиБ и 120 минут. В видео должна быть звуковая дорожка.</p>')


def import_page(request,db,*,pending=None,title="",source="",upload_selected=False):
    campus_lab.access(request.user)
    options=import_options(db,request.user)
    if not options:
        raise DomainError(403,"Для добавления лекции нужна назначенная учебная группа.")
    personal = request.user["role"] == "student"
    summary = ("Запись и материалы доступны только тебе." if personal
               else "Запись и готовые материалы будут доступны выбранной группе.")
    audience = hidden("scope", next(iter(options))) if personal else select_field("scope","Кому доступна запись",list(options.items()))
    controls=input_field("title","Название лекции",title,"required maxlength=120")+audience
    upload=form(request,IMPORT_PATH+"/upload",controls+upload_controls()+analysis_options()+button("Загрузить и обработать"),
                multipart=True,command=True,style="library-upload")
    source=input_field("url","Ссылка на страницу с лекцией или на видео",source,'required maxlength="1500" placeholder="https://…"', "url")
    hosts = lecture_imports.media_hosts()
    host_labels = [({lecture_imports.COMMONS_MEDIA: "Wikimedia Commons", lecture_imports.W3C_MEDIA: "W3C WAI"}.get(host, host))
                   for host in sorted(hosts)]
    sources = (f'<p class="meta">Подключённые источники · {e(", ".join(host_labels))}</p>' if hosts
               else '<p class="meta">Для автоматической обработки сейчас можно загрузить файл.</p>')
    if lecture_imports.public_links_enabled():
        sources = ('<p class="meta">Подойдёт ссылка на одно доступное видео YouTube, публичную страницу с открытым видео '
                   'или файл MP4 или WebM.</p>')
    linked=form(request,IMPORT_PATH+"/link",controls+source
                +'<p class="meta">Из записи со звуком подготовим расшифровку, конспект и домашнее задание.</p>'
                +sources+analysis_options()+button("Добавить лекцию по ссылке"),command=True)
    link_checked = "" if upload_selected else " checked"
    upload_checked = " checked" if upload_selected else ""
    picker = ('<fieldset class="library-import-tabs"><legend class="dean-visually-hidden">Источник записи</legend>'
              f'<label><input type="radio" name="lecture-source" value="link"{link_checked}>По ссылке</label>'
              f'<label><input type="radio" name="lecture-source" value="upload"{upload_checked}>Загрузить видео</label></fieldset>')
    body = ('<div class="library-import-source">' + picker
            + panel("Загрузить видео", upload, style="library-import-file")
            + panel("Добавить по ссылке", linked, style="library-import-link") + '</div>')
    current = pending.lecture if pending else (personal_import(db,request.user) if personal else None)
    if current:
        stage = job_state(current,current["queued"])
        message = pending.message if pending else "Сейчас обрабатывается личная лекция."
        details = f'<p class="meta">{e(stage["status"])}</p>'
        if stage["detail"]:
            details += f'<p class="meta">{e(stage["detail"])}</p>'
        if pending and upload_selected:
            details += "<p>Когда освободится очередь, выбери файл снова.</p>"
        body = (panel("Лекция в обработке", f'<p role="status">{e(message)}</p><p><strong>{e(current["title"])}</strong></p>'
                      + details + link(PATH + "/" + current["id"],"Открыть статус обработки","dean-action-link"),
                      style="library-current-job") + body)
    return page(request,"Добавить лекцию",body,summary,
                section="lecture-library", breadcrumb=back("/lecture-library", "К видеотеке"))


def staff_access(user):
    campus_lab.access(user)
    role(user,"staff")


def teacher_scope(db,user,value):
    scopes=[(r["group_id"],r["period_id"],r["subject_id"]) for r in coursework.teacher_scopes(db,user)]
    scope=tuple(value.split("|"))
    if scope not in scopes:
        raise DomainError(403,"Группа или дисциплина вне назначения преподавателя.")
    return scope


def create(request,db):
    user=request.user
    staff_access(user)
    scope=teacher_scope(db,user,request.field("scope"))
    title=field(request.field("title"),"Название",120)
    def insert():
        lid=identifier()
        db.execute("INSERT INTO lab_lecture(id,owner,university_id,group_id,period_id,subject_id,title,created) VALUES (?,?,?,?,?,?,?,?)",
                   (lid,user["id"],user["university_id"],*scope,title,timestamp()))
        return lid
    lid=services.command(db,user,"lab.lecture.create",request.field("request_id"),[scope,title],insert)
    return redirect(lid)


def import_link(request,db):
    campus_lab.access(request.user)
    source=request.field("url").strip()
    process=bool(lecture_imports.recording_source(source))
    if not process:
        from . import lecture_youtube
        parsed = lecture_imports.parsed_url(source)
        if parsed.hostname in lecture_youtube.HOSTS and not lecture_youtube.video_url(parsed):
            raise DomainError(422, "Укажи ссылку на одно видео YouTube.")
    lid = request.field("lecture")
    if lid:
        row = record(db, request.user, lid, owner=True)
        if not row["recording_url"]:
            raise DomainError(404, "У этой лекции нет ссылки на видео.")
        scope = "|".join(row[name] for name in ("group_id", "period_id", "subject_id"))
        title = row["title"]
    else:
        scope, title = request.field("scope"), request.field("title")
    return import_recording(request,db,scope,title,request.field("request_id"),
                            source=source,existing=lid,process=process,cloud=request.field("cloud")=="1",
                            use_model=request.field("model")=="1")


def import_upload(request,db):
    from .auth import valid_csrf

    campus_lab.access(request.user)
    with lecture_imports.temporary(lecture_imports.storage(db)) as path:
        fields=lecture_imports.read_upload(request.env,path,lambda csrf:valid_csrf(request.session,csrf))
        lid=fields.get("lecture","")
        if lid:
            row=record(db,request.user,lid,owner=True)
            if not row["recording_url"]:
                raise DomainError(404,"Добавление файла к этой лекции недоступно.")
            scope="|".join(row[name] for name in ("group_id","period_id","subject_id"))
            title=row["title"]
        else:
            scope,title=fields.get("scope",""),fields.get("title","")
        return import_recording(request,db,scope,title,fields.get("request_id",""),upload=path,existing=lid,
                                cloud=fields.get("cloud")=="1",use_model=fields.get("model")=="1")


def import_recording(request,db,scope,title,request_id,*,source=None,upload=None,existing="",process=True,cloud=False,use_model=False):
    user=request.user
    if scope not in import_options(db,user):
        raise DomainError(403,"Группа или дисциплина вне области доступа сотрудника.")
    scope=tuple(scope.split("|"))
    title=field(title,"Название",120)
    mime=lecture_imports.media_type(upload) if upload else None
    fingerprint=source
    if upload:
        with upload.open("rb") as stream:
            fingerprint=file_digest(stream,"sha256").hexdigest()

    stored, token = None, None
    def insert():
        nonlocal stored, token
        lid=existing or identifier()
        if user["role"] == "student" and process:
            check_private_import_limit(db, user)
        if existing:
            row=record(db,user,lid,owner=True)
            if row["recording_mime"] or row["transcript"] or processing(db,row):
                raise DomainError(409,"Лекция уже обрабатывается или содержит видео.")
            db.execute("""UPDATE lab_lecture SET recording_url=coalesce(?,recording_url),
                       recording_mime=?,analysis_until=NULL,analysis_token=NULL,
                       analysis_error=NULL,analysis_status=NULL WHERE id=?""", (source,mime,lid))
        else:
            created=timestamp()
            visibility = "private" if user["role"] == "student" else "group"
            db.execute("""INSERT INTO lab_lecture(id,owner,university_id,group_id,period_id,subject_id,title,
                          created,ended,recording_url,recording_mime,visibility) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                       (lid,user["id"],user["university_id"],*scope,title,created,created,source,mime,visibility))
        if upload:
            target=lecture_imports.storage(db)/lid
            upload.replace(target)
            stored=target
        if process:
            token=queue(db,lid,cloud=cloud,use_model=use_model)
        return lid

    try:
        lid=services.command(db,user,"lab.lecture.import",request_id,
                             [scope,title,mime,fingerprint,existing,cloud,use_model],insert)
    except PersonalLecturePending as error:
        return 409,import_page(request,db,pending=error,title=title,source=source or "",
                               upload_selected=bool(upload)),[]
    except BaseException:
        if stored:
            stored.unlink(missing_ok=True)
        raise
    if token:
        launch(db,lid,token,request.ai,cloud,use_model,background=True)
    return redirect(lid)


def redirect(lid):
    return 303,b"",[("Location",PATH+"/"+lid)]


def record(db,user,lid,*,owner=False):
    campus_lab.access(user)
    row=require(db.execute("SELECT * FROM lab_lecture WHERE id=? AND university_id=?",(lid,user["university_id"])).fetchone())
    if row["owner"]!=user["id"] and (owner or row["visibility"] != "group" or user["role"]!="student" or
            (row["group_id"],row["period_id"])!=(user["group_id"],user["period_id"])):
        raise DomainError(404,"Лекция недоступна.")
    return row


def lecture_homework(row):
    text = row["homework"] or ""
    if (row["transcript"] and "локальная выборка фрагментов" in (row["source"] or "")
            and text.startswith(LEGACY_HOMEWORK_START)
            and "\n2. Приведи пример, который помогает понять мысль «" in text):
        return lecture_analysis.local_homework(row["transcript"])
    return text


def task_description(db, user, task):
    old = task["description"]
    if task["lecture_id"] and old.startswith(LEGACY_HOMEWORK_START):
        try:
            row = record(db, user, task["lecture_id"])
        except DomainError as error:
            if error.status != 404:
                raise
        else:
            if old == (row["homework"] or "")[:1600].strip():
                return lecture_homework(row)
    return old


def homework_task_id(db, user, lid):
    link = db.execute("SELECT task_id FROM lecture_homework_task WHERE lecture_id=? AND owner_id=?",
                      (lid, user["id"])).fetchone()
    return link["task_id"] if link else None


def personal_import(db, user):
    return db.execute("""
        SELECT l.*, j.queued FROM lab_lecture l LEFT JOIN lecture_analysis_job j ON j.lecture_id=l.id
        WHERE l.university_id=? AND l.owner=? AND l.visibility='private'
          AND (l.analysis_until>? OR j.lecture_id IS NOT NULL)
        ORDER BY j.queued,l.created,l.id LIMIT 1
    """, (user["university_id"], user["id"], timestamp())).fetchone()


def check_private_import_limit(db, user):
    current = personal_import(db, user)
    if current:
        raise PersonalLecturePending(current)


def segment_count(db,lid):
    return db.execute("SELECT count(*) FROM lab_segment WHERE lecture=?",(lid,)).fetchone()[0]


def notes_markup(notes):
    return ''.join(f'<h3>{e(line[3:])}</h3>' if line.startswith("## ") else f'<p class="lab-prose">{e(line)}</p>'
                   for line in notes.splitlines() if line.strip())


def lecture_quiz(row):
    if row["quiz_json"] is not None:
        return json.loads(row["quiz_json"])
    return lecture_analysis.local_quiz(row["transcript"]) if row["transcript"] else []


def answer_quiz(request, db, lid):
    with write(db):
        row = record(db, request.user, lid)
        quiz = lecture_quiz(row)
        if not quiz:
            raise DomainError(404, "Тест по лекции ещё не готов.")
        question = lecture_library.seconds_value(request.field("question"), len(quiz) - 1, default=-1)
        if question < 0:
            raise DomainError(422, "Вопрос не найден. Открой тест заново.")
        choice = lecture_library.seconds_value(request.field("answer"), len(quiz[question]["options"]) - 1, default=-1)
        if choice < 0:
            raise DomainError(422, "Выбери один вариант ответа.")
        if row["quiz_json"] is None:
            db.execute("UPDATE lab_lecture SET quiz_json=? WHERE id=?", (json.dumps(quiz, ensure_ascii=False), lid))
        db.execute("""INSERT INTO lab_lecture_quiz_answer(lecture_id,account_id,question,choice) VALUES (?,?,?,?)
                      ON CONFLICT(lecture_id,account_id,question) DO UPDATE SET choice=excluded.choice""",
                   (lid, request.user["id"], question, choice))
    return lecture_library.redirect(f"{PATH}/{lid}#question-{question}")


def detail(request,db,lid):
    row=record(db,request.user,lid)
    request.allow_lecture_media=True
    url=PATH+"/"+lid
    owner=row["owner"]==request.user["id"]
    count=segment_count(db,lid)
    body=back(lecture_library.PATH,"К лекциям")
    imported=row["recording_mime"] or row["recording_url"]
    progress = job_state(row,queued_at(db,lid))
    quiz = lecture_quiz(row)
    state="Видеозапись" if imported else ("Запись эфира" if row["ended"] else "Прямой эфир")
    if row["transcript"]:
        body+=('<nav class="library-switch" aria-label="Материалы лекции"><a href="#notes">Конспект</a> '
               '<a href="#homework">Домашнее задание</a> '
               + ('<a href="#practice">Тест по лекции</a> ' if quiz else '')
               + '<a href="#transcript">Расшифровка</a></nav>')
    if row["recording_mime"]:
        if row["recording_mime"].startswith("audio/"):
            state = "Аудиодорожка"
            player = (f'<div class="library-watch"><audio controls preload="metadata" src="{url}/recording">'
                      'Твой браузер не поддерживает воспроизведение аудио.</audio></div>')
        else:
            player=(f'<div class="library-watch"><video controls playsinline preload="metadata" src="{url}/recording">'
                    'Твой браузер не поддерживает воспроизведение видео.</video></div>')
        if row["recording_url"]:
            player+=('<p><a class="dean-action-link" referrerpolicy="no-referrer" '
                     f'href="{e(row["recording_url"])}">Открыть источник лекции</a></p>')
    elif imported:
        player=('<p><a class="dean-action-link" referrerpolicy="no-referrer" '
                f'href="{e(row["recording_url"])}">Открыть видео по ссылке</a></p>')
    else:
        player=(f'<div data-lecture-view="{url}"><video class="lab-recording" controls playsinline preload="metadata" '
                + (f'src="{url}/segments/0"' if count else '') + '></video>'
                '<p role="status" data-view-status></p><button type="button" data-view-start>Смотреть и слушать</button></div>')
    body+=panel(state,player)
    if owner and not row["ended"]:
        media_form=(f'<div data-lecture-publisher="{url}" data-count="{count}" data-csrf="{e(request.csrf)}">'
                    '<label class="check"><input type="checkbox" data-use-camera>Передавать видео с камеры</label>'
                    '<button type="button" data-broadcast>Начать передачу микрофона</button> '
                    '<button type="button" data-pause disabled>Остановить передачу</button><p role="status" data-publish-status></p></div>')
        attach=form(request,url+"/upload",'<label>Запись до 2 МБ<input type="file" name="file" accept="audio/*,video/mp4,video/webm" required></label>'+button("Добавить запись"),multipart=True,command=True)
        body+=panel("Управление эфиром",media_form+attach+
                    form(request,url+"/sample",button("Передать учебную аудиозапись"),command=True)+
                    form(request,url+"/end",button("Завершить эфир"),command=True))
    if row["transcript"]:
        body+=panel("Конспект",lecture_library.pdf_links(url,"notes")
                    +notes_markup(row["notes"])+'<p class="meta">'+e(row["source"])+'</p>',key="notes")
        homework = lecture_homework(row)
        homework_action = ""
        if request.user["role"] == "student" and homework:
            task_id = homework_task_id(db, request.user, lid)
            homework_action = (link("/tasks/" + task_id, "Открыть задачу", "dean-action-link") if task_id else
                               form(request, url+"/task", button("Добавить в мои задачи"), command=True))
        body+=panel("Домашнее задание", lecture_library.pdf_links(url,"homework")
                    +notes_markup(homework)+homework_action, key="homework")
        if quiz:
            from .lecture_library_views import quiz_panel

            answers = dict(db.execute("SELECT question,choice FROM lab_lecture_quiz_answer WHERE lecture_id=? AND account_id=?",
                                      (lid, request.user["id"])))
            body += '<div class="library-page">' + quiz_panel(request, quiz, answers, url, title="Тест по лекции") + '</div>'
        body+=panel("Расшифровка",'<details><summary>Читать расшифровку целиком</summary><p class="lab-prose">'
                    +e(row["transcript"])+'</p></details>',key="transcript")
        if owner and row["subject_id"]:
            due=local_value(timestamp(now()+timedelta(days=7)),request.user["timezone"]).strftime("%Y-%m-%dT%H:%M")
            controls=input_field("title","Название работы",row["title"][:99]+" · практика","required maxlength=120")
            controls+=textarea("description","Домашнее задание",2000,required=True,value=homework)
            controls+=input_field("due","Сдать до",due,"required","datetime-local")
            controls+=textarea("rubric","Критерии",1000,required=True,value="Понимание материала | 5\nОбоснование ответа | 5")
            body+=panel("Работа для группы",form(request,url+"/assignment",controls+button("Сохранить черновик работы"),command=True))
    elif row["ended"] and (count or imported) and progress["running"]:
        controls = (form(request,url+"/stop",'<button class="quiet">Остановить обработку</button>')
                    if owner else "")
        body += panel("Материалы лекции", f'<div data-lecture-progress="{url}/status" class="library-job-progress">'
                      f'<p role="status" aria-atomic="true"><strong data-job-status>{e(progress["status"])}</strong>'
                      f'<span class="meta" data-job-detail>{e(progress["detail"])}</span></p>'
                      '<p class="meta" data-job-connection hidden>Не удалось обновить статус. Проверим снова.</p>'
                      '<p>Можешь закрыть вкладку. Обработка продолжится на сервере.</p></div>'
                      f'{controls}', style="library-current-job")
    elif owner and row["ended"] and (count or imported):
        controls=""
        if progress["error"]:
            controls += f'<p role="alert">{e(progress["error"])}</p>'
        auto_link=bool(row["recording_url"] and lecture_imports.recording_source(row["recording_url"]))
        if count or row["recording_mime"] or auto_link:
            caption = ("Повторить обработку" if progress["error"] else "Найти видео и обработать"
                       if auto_link and not row["recording_mime"] else "Расшифровать и подготовить материалы")
            controls+=form(request,url+"/analyse",analysis_options()
                           +f'<button type="submit" name="background" value="1">{caption}</button>',command=True)
        elif row["recording_url"]:
            controls+='<p>Ссылка сохранена. Этот источник пока не подключён для автоматической обработки.</p>'
        if imported and not row["recording_mime"]:
            source_field = input_field("url", "Новая HTTPS-ссылка", row["recording_url"],
                                       'required maxlength="1500"', "url")
            controls += ('<details class="library-import-options"><summary>Заменить ссылку</summary>'
                         + form(request, IMPORT_PATH+"/link", hidden("lecture",lid)+source_field+analysis_options()
                                + button("Сохранить новую ссылку"), command=True) + '</details>')
            controls+=form(request,IMPORT_PATH+"/upload",hidden("lecture",lid)+analysis_options()+upload_controls()
                           +button("Загрузить исходный файл и обработать"),multipart=True,command=True,style="library-upload")
        body+=panel("Материалы лекции",controls)
    elif imported:
        message=("Конспект, домашнее задание и тест появятся после обработки записи." if row["recording_mime"]
                 else "Расшифровка, конспект, домашнее задание и тест станут доступны после обработки исходного видео.")
        body+=panel("Материалы лекции",f"<p>{e(progress['error'] or message)}</p>")
    return page(request,row["title"],body,section="lecture-library")


def receive(request,db,lid,mode):
    row=record(db,request.user,lid,owner=True)
    body=SAMPLE.read_bytes() if mode=="sample" else (request.upload[1] if request.upload else b"")
    if not body or len(body)>2*1024*1024:
        raise DomainError(413,"Выбери запись от 1 байта до 2 МБ.")
    mime,_=lecture_analysis.audio_kind(body)
    seq=request.field("seq")
    def append():
        if record(db,request.user,lid,owner=True)["ended"]:
            raise DomainError(409,"Эфир уже завершён.")
        index,total=db.execute("SELECT count(*),coalesce(sum(length(media)),0) FROM lab_segment WHERE lecture=?",(lid,)).fetchone()
        if seq and str(index)!=seq:
            raise DomainError(409,"Эфир изменился в другой вкладке. Открой его заново.")
        if total+len(body)>LIMIT or index>=1900:
            raise DomainError(413,"Достигнут лимит записи. Заверши этот эфир.")
        db.execute("INSERT INTO lab_segment VALUES (?,?,?,?)",(lid,index,mime,body))
        return str(index)
    index=services.command(db,request.user,"lab.segment."+lid,request.field("request_id"),[seq,sha256(body).hexdigest()],append)
    if seq:
        return 200,json.dumps({"seq":int(index)}),[("Content-Type","application/json")]
    return redirect(lid)


def finish(request,db,lid):
    record(db,request.user,lid,owner=True)
    with write(db):
        db.execute("UPDATE lab_lecture SET ended=coalesce(ended,?) WHERE id=?",(timestamp(),lid))
    return redirect(lid)


def manifest(request,db,lid):
    row=record(db,request.user,lid)
    return 200,json.dumps({"count":segment_count(db,lid),"live":row["ended"] is None}),[("Content-Type","application/json"),("Cache-Control","no-store")]


def media(request,db,lid,seq):
    from .lecture_studio import media_range
    record(db,request.user,lid)
    row=require(db.execute("SELECT mime,media FROM lab_segment WHERE lecture=? AND seq=?",(lid,int(seq))).fetchone())
    return media_range(row["media"],row["mime"],request.env.get("HTTP_RANGE",""))


def recording(request,db,lid):
    row=record(db,request.user,lid)
    if not row["recording_mime"]:
        raise DomainError(404,"Запись пока недоступна.")
    path=lecture_imports.storage(db)/lid
    if not path.is_file():
        raise DomainError(503,"Не удалось открыть видео. Сообщи об этом преподавателю.")
    return lecture_library.stream_video(request,path,row["recording_mime"])


def pdf(request,db,lid,kind):
    row=record(db,request.user,lid)
    text = lecture_homework(row) if kind == "homework" else row[kind]
    if not row["transcript"] or not text:
        raise DomainError(404,"Материал ещё не готов.")
    saved=db.execute("SELECT notes,homework FROM lecture_pdf WHERE lecture_id=?",(lid,)).fetchone()
    payload=(saved[kind] if saved and text == row[kind] else pdf_writer.render_lecture(
        row["title"], lecture_library.PDF_TITLES[kind], text.splitlines(), row["source"] or ""))
    return lecture_library.pdf_response(request,kind,lid,row["title"],payload)


def queued_at(db,lid):
    job = db.execute("SELECT queued FROM lecture_analysis_job WHERE lecture_id=?",(lid,)).fetchone()
    return job["queued"] if job else None


def job_state(row,queued):
    moment = now()
    active = bool(row["analysis_until"] and row["analysis_until"]>timestamp(moment))
    result = {"running": active or bool(queued), "ready": bool(row["transcript"]), "error": "",
              "status": "", "detail": ""}
    if result["ready"]:
        result.update(status="Материалы готовы", running=False)
    elif active:
        result["status"] = row["analysis_status"] or "Готовим запись"
    elif queued:
        if row["analysis_until"]:
            result.update(status="Ожидаем повторного запуска",
                          detail="Предыдущая попытка прервалась. Можно дождаться повторного запуска или остановить обработку.")
        else:
            result["status"] = "В очереди на обработку"
            if queued <= timestamp(moment-ANALYSIS_QUEUE_NOTICE):
                result["detail"] = "Запуск задерживается. Можно подождать или остановить обработку в карточке лекции."
    elif row["analysis_error"] or row["analysis_until"]:
        error = row["analysis_error"] or ANALYSIS_INTERRUPTED
        if error in {LEGACY_INTERRUPTED, "Обработка прервалась. Запись сохранена, можно повторить."}:
            error = ANALYSIS_INTERRUPTED
        caption = "Обработка остановлена" if error == ANALYSIS_STOPPED else "Материалы не готовы"
        result.update(status=caption, error=error)
    return result


def processing(db,row):
    return job_state(row,queued_at(db,row["id"]))["running"]


def status(request, db, lid):
    row = record(db, request.user, lid)
    data = job_state(row,queued_at(db,lid))
    return 200,json.dumps(data,ensure_ascii=False),[("Content-Type","application/json"),("Cache-Control","no-store")]


def teaching_material(db,row,token,transcript,engine,use_model):
    reason = ""
    if use_model and engine.enabled and getattr(engine,"can_chat",True):
        def prepare():
            notes,homework,source,quiz=lecture_analysis.teaching_material(transcript,engine,True)
            return {"notes":notes,"homework":homework,"source":source,"quiz":quiz}
        try:
            data=ai.once(db,{"id":row["owner"]},engine,"lecture",token,transcript.encode(),prepare)
            if not data.get("status"):
                return data["notes"],data["homework"],data["source"],data.get("quiz") or lecture_analysis.local_quiz(transcript)
        except DomainError:
            pass
        reason = "подготовка с ИИ не завершилась"
    elif use_model:
        reason = "подготовка с ИИ сейчас недоступна"
    notes, homework, source, quiz = lecture_analysis.teaching_material(transcript,engine,False)
    return notes, homework, source + (f" · {reason}" if reason else ""), quiz


def analysis_job(path, lid, token, engine, cloud, use_model):
    stopped = Event()

    def heartbeat():
        with closing(connect(path, create=False)) as connection:
            while not stopped.wait(25):
                try:
                    refreshed = connection.execute("UPDATE lab_lecture SET analysis_until=? WHERE id=? AND analysis_token=?",
                                                   (timestamp(now()+ANALYSIS_LEASE),lid,token))
                    if not refreshed.rowcount:
                        return
                except sqlite3.OperationalError:
                    continue

    pulse = Thread(target=heartbeat, daemon=True)
    pulse.start()
    stage = "при подготовке записи"
    try:
        with closing(connect(path, create=False)) as db:
            def require_active():
                if not db.execute("SELECT 1 FROM lab_lecture WHERE id=? AND analysis_token=?", (lid,token)).fetchone():
                    raise DomainError(409, "Началась другая попытка обработки.")

            def report(stage):
                owned = db.execute("UPDATE lab_lecture SET analysis_status=? WHERE id=? AND analysis_token=?", (stage,lid,token))
                if not owned.rowcount:
                    raise DomainError(409, "Началась другая попытка обработки.")
            # Close each read cursor before the decoder runs, so progress writes see current WAL state.
            count = segment_count(db,lid)
            row = db.execute("SELECT owner,title,recording_mime,recording_url FROM lab_lecture WHERE id=?",(lid,)).fetchone()
            if row["recording_mime"] or row["recording_url"]:
                root=lecture_imports.storage(db)
                recording=root/lid
                if not row["recording_mime"]:
                    stage = "при получении записи по ссылке"
                    with lecture_imports.temporary(root) as incoming:
                        mime=lecture_imports.download(row["recording_url"],incoming,progress=report,
                                                      require_active=require_active)
                        with write(db):
                            downloaded=db.execute("""UPDATE lab_lecture SET recording_mime=?
                                                    WHERE id=? AND analysis_token=?""",(mime,lid,token))
                            if not downloaded.rowcount:
                                raise DomainError(409,"Началась другая попытка обработки.")
                            incoming.replace(recording)
                segments=(recording,)
            else:
                segments = (db.execute("SELECT media FROM lab_segment WHERE lecture=? AND seq=?", (lid,n)).fetchone()[0] for n in range(count))
            stage = "при подготовке звуковой дорожки и расшифровки"
            report("Готовим звуковую дорожку")
            transcript,asr=lecture_analysis.transcribe(segments,engine,cloud,progress=report,
                                                       require_active=require_active)
            stage = "при подготовке конспекта, домашнего задания и теста"
            report("Готовим конспект, домашнее задание и тест")
            notes,tasks,source,quiz=teaching_material(db,row,token,transcript,engine,use_model)
            source=asr+" · "+source
            documents=[pdf_writer.render_lecture(row["title"],lecture_library.PDF_TITLES[kind],text.splitlines(),source)
                       for kind,text in (("notes",notes),("homework",tasks))]
            stage = "при сохранении материалов"
            with write(db):
                saved = db.execute("""UPDATE lab_lecture SET transcript=?,notes=?,homework=?,source=?,quiz_json=?,analysis_until=NULL,
                              analysis_token=NULL,analysis_status=NULL,analysis_error=NULL WHERE id=? AND analysis_token=?""",
                           (transcript,notes,tasks,source,json.dumps(quiz,ensure_ascii=False),lid,token))
                if saved.rowcount:
                    db.execute("INSERT INTO lecture_pdf(lecture_id,notes,homework) VALUES (?,?,?)",(lid,*documents))
                    db.execute("DELETE FROM lecture_analysis_job WHERE lecture_id=?",(lid,))
    except Exception as error:
        message = (error.message if isinstance(error,DomainError) else
                   f"Обработка прервалась {stage}. Лекция осталась в списке, обработку можно повторить.")
        with closing(connect(path, create=False)) as db, write(db):
            saved = db.execute("UPDATE lab_lecture SET analysis_until=NULL,analysis_token=NULL,analysis_status=NULL,analysis_error=? WHERE id=? AND analysis_token=?",
                               (message,lid,token))
            if saved.rowcount:
                db.execute("DELETE FROM lecture_analysis_job WHERE lecture_id=?",(lid,))
        if not isinstance(error, DomainError):
            logging.getLogger(__name__).exception("Lecture analysis interrupted")
    finally:
        stopped.set()
        pulse.join()


def queue(db,lid,*,cloud,use_model):
    if getenv("DEAN_LECTURE_WORKER")=="1":
        db.execute("""INSERT INTO lecture_analysis_job(lecture_id,queued,cloud,use_model) VALUES (?,?,?,?)""",
                   (lid,timestamp(),int(cloud),int(use_model)))
        db.execute("""UPDATE lab_lecture SET analysis_until=NULL,analysis_token=NULL,
                      analysis_status='В очереди на обработку',analysis_error=NULL WHERE id=?""", (lid,))
        return None
    token=identifier()
    db.execute("""UPDATE lab_lecture SET analysis_until=?,analysis_token=?,analysis_status='Готовим аудио',
                  analysis_error=NULL WHERE id=?""", (timestamp(now()+ANALYSIS_LEASE),token,lid))
    return token


def launch(db,lid,token,engine,cloud,model,*,background):
    path = db.execute("PRAGMA database_list").fetchone()[2]
    args=(path,lid,token,engine,cloud,model)
    if background:
        Thread(target=analysis_job,args=args,daemon=True).start()
    else:
        analysis_job(*args)


def analyse(request,db,lid):
    cloud,use_model=request.field("cloud")=="1", request.field("model")=="1"
    try:
        with write(db):
            row=record(db,request.user,lid,owner=True)
            if row["transcript"] or processing(db,row):
                return redirect(lid)
            auto_link=bool(row["recording_url"] and lecture_imports.recording_source(row["recording_url"]))
            if not row["ended"] or not (auto_link or row["recording_mime"] or segment_count(db,lid)):
                raise DomainError(409,"Сначала добавь видео или заверши эфир со звуковой записью.")
            if row["visibility"] == "private":
                check_private_import_limit(db, request.user)
            token=queue(db,lid,cloud=cloud,use_model=use_model)
    except PersonalLecturePending as error:
        return 409,import_page(request,db,pending=error),[]
    if token:
        launch(db,lid,token,request.ai,cloud,use_model,background=request.field("background")=="1")
    return redirect(lid)


def stop_analysis(request,db,lid):
    with write(db):
        row = record(db,request.user,lid,owner=True)
        if processing(db,row):
            db.execute("""UPDATE lab_lecture SET analysis_until=NULL,analysis_token=NULL,
                          analysis_status=NULL,analysis_error=? WHERE id=?""", (ANALYSIS_STOPPED,lid))
            db.execute("DELETE FROM lecture_analysis_job WHERE lecture_id=?", (lid,))
    return redirect(lid)


def assignment(request,db,lid):
    staff_access(request.user)
    row=record(db,request.user,lid,owner=True)
    if not row["transcript"]:
        raise DomainError(409,"Сначала подготовь материалы.")
    fields={k:request.field(k) for k in ("title","description","due","rubric")}
    fields.update(scope="|".join(row[k] for k in ("group_id","period_id","subject_id")),response_mode="text",attempt_limit="2")
    wid=coursework.save_assignment(db,request.user,fields,request.field("request_id"))
    return 303,b"",[("Location","/staff/work/"+wid)]


def personal_task(request, db, lid):
    user = request.user
    role(user, "student")
    record(db, user, lid)

    def apply():
        row = record(db, user, lid)
        homework = lecture_homework(row).strip()
        if not homework:
            raise DomainError(409, "Домашнее задание ещё не готово.")
        existing = homework_task_id(db, user, lid)
        if existing:
            return existing
        description = homework if len(homework) <= 1600 else "Полный текст задания находится в карточке лекции."
        values = workspace.task_values(db, user, {"title": "Домашняя работа · " + row["title"][:100],
                                                 "description": description})
        task_id = workspace.add_task(db, user, values)
        db.execute("INSERT INTO lecture_homework_task (lecture_id, owner_id, task_id) VALUES (?, ?, ?)",
                   (lid, user["id"], task_id))
        return task_id

    task_id = services.command(db, user, "lecture.task", request.field("request_id"), [lid], apply)
    return 303, b"", [("Location", "/tasks/" + task_id)]
