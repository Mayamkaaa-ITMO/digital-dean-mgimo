"""Create the personal testing stand once, without modifying any previous demo."""
import fcntl
import hashlib
import json
import os
from contextlib import closing
from datetime import datetime, time, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory

from dean import activity_extensions, calendar_import, coursework, personal_study, photo_import, public_schedule, services, study_materials, workspace
from dean.config import WebConfig
from dean.db import connect, database_path, migrate, write
from dean.domain import identifier
from dean.schedule import local_value
from dean.seed import ZONE, credentials_path, seed, seed_id
from dean.web import create_app
from tests.test_dean import Client


def submit(client, page, action, **fields):
    status, headers, _ = client.request(action, {**client.fields(page, action), **fields})
    if status != 303:
        raise RuntimeError(f"Не удалось подготовить демо через {action}: HTTP {status}")
    return headers["Location"]


def add_assistant_sample(db):
    user = db.execute("SELECT * FROM account WHERE username='student-a' AND role='student'").fetchone()
    if user is None:
        return
    sid = personal_study.create(db, user, "Проектная мастерская (демо)", None, "", seed_id("qa:assistant:subject"))
    if not db.execute("SELECT 1 FROM personal_subject WHERE id=? AND owner_id=?", (sid, user["id"])).fetchone():
        return
    study_materials.add(db, user, sid, {
        "kind":"note", "title":"Памятка по импорту расписания",
        "value":"Для фотоимпорта подготовь JPEG или PNG с одной неделей. Выбери дату нужной недели. "
                "После распознавания сверь день, время начала и конца, название предмета и аудиторию. "
                "Исправь ошибки в черновике и подтверди добавление пар. При недоступном распознавании используй импорт CSV."
    }, None, seed_id("qa:assistant:material"))


def populate(path):
    today = datetime.now(ZONE).date()
    monday = today - timedelta(days=today.weekday())
    with closing(connect(path)) as db:
        migrate(db)
        seed(db, path, anchor=today)
        add_assistant_sample(db)
        if os.environ.get("DEAN_EXT_LECTURE_STUDIO") == "1":
            coursework.grant_demo(db, "staff", "demo-ai", "databases")
    passwords = json.loads(credentials_path(path).read_text())
    app = create_app(path, WebConfig())
    student, operator = Client(app), Client(app)
    for client, name in ((student, "student-a"), (operator, "staff")):
        if client.login(name, passwords[name])[0] != 303:
            raise RuntimeError("Не удалось открыть демо-кабинет.")
    form = "/services/enrolment-certificate"
    for purpose, state in (("Для учебной библиотеки", "ready"), ("Для участия в конкурсе", "needs_changes")):
        req = submit(student, form, form, purpose=purpose, delivery="electronic")
        manage = "/staff" + req
        submit(operator, req, manage)
        if state == "ready":
            submit(operator, req, manage)
        else:
            submit(operator, req, manage + "/needs_changes", note="Укажи название конкурса.")
    with closing(connect(path)) as db:
        user = dict(db.execute("SELECT * FROM account WHERE username='student-a'").fetchone(), timezone=ZONE.key)
        staff = dict(db.execute("SELECT * FROM account WHERE username='staff'").fetchone(), timezone=ZONE.key)
        rooms = activity_extensions.rooms()
        with write(db):
            for lesson, room in (("a1", "b1-1012"), ("a2", "b2-2018a"), ("a3", "b1-1012"), ("a4", "n4-436"), ("a5", "n4-409")):
                db.execute("UPDATE schedule_event SET room=?,room_id=NULL WHERE id=?", (rooms[room], seed_id(lesson)))
        eid = seed_id("a3")
        row = db.execute("SELECT * FROM schedule_event WHERE id=?", (eid,)).fetchone()
        def shifted(field):
            return (local_value(row[field], ZONE.key) + timedelta(minutes=30)).strftime("%Y-%m-%dT%H:%M")
        services.move_lesson(db, staff, eid, row["version"], shifted("starts_at"), shifted("ends_at"),
                             rooms["b2-2018a"], identifier())
        workspace.save_task(db, user, {"title":"Подготовить вопросы к семинару", "subject":"databases",
                            "due":f"{today + timedelta(days=2)}T17:00"}, identifier())
        example = {"events":[{"day":2,"subject":"Пример по фото · Академическое письмо","kind":"Семинар",
                               "room":"2018а","start":"11:00","end":"12:30"}]}
        payload = photo_import.schedule_draft(example, user, ZONE.key, monday, dates_enabled=True)
        data = json.dumps(payload, ensure_ascii=False).encode()
        calendar_import.start(db, user, public_schedule.context(db,user), "draft", data, identifier(),
                              photo=(monday.isoformat(), hashlib.sha256(data).hexdigest()))
    form = "/staff/manage/resources/new"
    for offset, title, enrolled in ((2, "Практикум по переговорам", True), (3, "День студенческих проектов", False)):
        start = datetime.combine(today + timedelta(days=offset), time(17, 0))
        detail = submit(operator, form+"?kind=activity", form, group="demo-ai", kind="activity", title=title,
                        category="Практикум", host="Студенческий оргкомитет", rules="Приходи за пять минут.",
                        place="Кампус", map_room="b2-2018a", starts=start.isoformat(timespec="minutes"),
                        ends=(start+timedelta(hours=1)).isoformat(timespec="minutes"), capacity="12")
        submit(operator, detail, detail+"/publish")
        if enrolled:
            with closing(connect(path)) as db:
                sid = db.execute("SELECT id FROM reservation_slot WHERE bookable_id=?",(detail.rsplit("/",1)[1],)).fetchone()[0]
            window = "/slots/" + sid
            submit(student, window, window+"/book", ack="1")


def main():
    target = database_path()
    parent = target.parent
    parent.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    with (parent.parent / (parent.name + ".init.lock")).open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if target.is_file():
            with closing(connect(target, create=False)) as db:
                migrate(db)
            return
        if parent.exists():
            raise SystemExit("Каталог демо уже существует без базы. Выбери новую папку для демо.")
        with TemporaryDirectory(dir=parent.parent, prefix=".final-demo-") as temp:
            staged = Path(temp) / "complete"
            staged.mkdir(mode=0o700)
            populate(staged / target.name)
            os.rename(staged, parent)
    print("Учебная база готова.")



if __name__ == "__main__":
    main()
