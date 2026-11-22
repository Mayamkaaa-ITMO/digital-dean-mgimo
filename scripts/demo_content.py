"""Populate the shipped demo accounts through the same services as the cabinet."""
from datetime import timedelta

from dean import activity_extensions, application_forms, applications, campus, courses, coursework, equipment
from dean import lending, personal_study, portfolio, practice, reminders, reservations, services, study_materials
from dean import teams, workspace
from dean.clock import now
from dean.db import write
from dean.domain import timestamp
from dean.fixtures import demo_id
from dean.seed import SOURCE, UNIVERSITY, ZONE, at, seed_id


LESSONS = {
    "demo-ai": (
        ("algorithms", "Лекция", "b1-1012", "Ада Кремнева"),
        ("databases", "Практика", "b2-2018a", "Олег Полярисов"),
        ("english", "Семинар", "n4-423", "Нина Ясноречевая"),
    ),
    "demo-energy": (
        ("physics", "Лекция", "b1-1011", "Иван Грозосветов"),
        ("circuits", "Лабораторная", "b2-2011", "Лада Меднолучева"),
        ("graphics", "Практика", "b1-1010", "Тимур Клёновектор"),
    ),
}

WEEK = (
    ((0, 9), (1, 11)),
    ((2, 10), (0, 13)),
    ((1, 9), (0, 11), (2, 14)),
    ((0, 11), (1, 14)),
    ((2, 10), (1, 12)),
)

SEEDED_ROOMS = (
    ("a1", "ДЕМО · А-214", "b1-1012"),
    ("a2", "ДЕМО · А-108", "b2-2018a"),
    ("a3", "ДЕМО · Б-102", "n4-423"),
    ("a4", "ДЕМО · А-214", "b1-1012"),
    ("a5", "ДЕМО · А-108", "b2-2018a"),
    ("b1", "ДЕМО · В-205", "b1-1011"),
    ("b2", "ДЕМО · В-310", "b2-2011"),
    ("b3", "ДЕМО · В-225", "b1-1010"),
)

COWORKING = (
    ("showcase-coworking", "Коворкинг «Маяк»", "b1-1013", "Шесть мест, доска, розетки и Wi-Fi"),
    ("showcase-meeting", "Переговорная «Сфера»", "b1-1014", "Четыре места, экран и розетки"),
)


def saved(db, user, operation, key):
    return db.execute("SELECT result_id FROM command WHERE university_id=? AND actor_id=? "
                      "AND operation=? AND request_id=?",
                      (UNIVERSITY, user["id"], operation, seed_id(key))).fetchone()


def schedule_examples(db, students, today):
    rooms = activity_extensions.rooms()
    for ref, old_alias, key in SEEDED_ROOMS:
        if key in rooms:
            alias = rooms[key]
            db.execute("""UPDATE schedule_event SET room=?,room_id=?
                WHERE id=? AND university_id=? AND source_id=? AND source_event_id=? AND version=1 AND room=?""",
                       (alias, campus.room_id(db, UNIVERSITY, alias), seed_id(ref), UNIVERSITY, SOURCE, ref, old_alias))
    for user in students.values():
        group, period = user["group_id"], user["period_id"]
        if group not in LESSONS:
            continue
        semester = db.execute("SELECT starts_on,ends_on FROM academic_period WHERE university_id=? AND id=?",
                              (UNIVERSITY, period)).fetchone()
        if semester is None:
            continue
        for offset in range(-28, 29):
            day = today + timedelta(days=offset)
            weekdays = day.weekday()
            if weekdays >= len(WEEK) or not semester["starts_on"] <= day.isoformat() <= semester["ends_on"]:
                continue
            for index, (topic, hour) in enumerate(WEEK[weekdays]):
                subject, kind, key, teacher = LESSONS[group][topic]
                starts, ends = at(day, hour), at(day, hour + 1, 30)
                ref = f"showcase:lesson:{group}:{period}:{day}:{index}"
                db.execute("""
                    INSERT INTO schedule_event
                        (id,university_id,group_id,period_id,subject_id,starts_at,ends_at,timezone,kind,room,teacher,
                         source_id,source_event_id,origin)
                    SELECT ?,?,?,?,?,?,?,?,?,?,?,?,?,'demo_university'
                    WHERE NOT EXISTS (
                        SELECT 1 FROM schedule_event WHERE university_id=? AND group_id=? AND period_id=?
                        AND starts_at<? AND ends_at>?
                    ) ON CONFLICT DO NOTHING
                """, (seed_id(ref), UNIVERSITY, group, period, subject, starts, ends, ZONE.key, kind,
                      rooms.get(key, "Кампус"), teacher, SOURCE, ref, UNIVERSITY, group, period, ends, starts))


def slot_example(db, ref, offer, starts, ends, capacity):
    db.execute("""
        INSERT INTO reservation_slot (id,university_id,bookable_id,starts_at,ends_at,capacity)
        SELECT ?,university_id,id,?,?,? FROM bookable WHERE id=? AND university_id=?
        AND NOT EXISTS (
            SELECT 1 FROM reservation_slot WHERE bookable_id=bookable.id AND starts_at<? AND ends_at>?
        ) ON CONFLICT DO NOTHING
    """, (seed_id(ref), starts, ends, capacity, offer, UNIVERSITY, ends, starts))


def booking_examples(db, teacher, today):
    for code, title, key, equipment_text in COWORKING:
        bid = demo_id(UNIVERSITY, "bookable", code)
        room = activity_extensions.rooms().get(key, "Кампус")
        db.execute("""
            INSERT INTO bookable
                (id,university_id,manager_id,kind,title,category,place,equipment,rules,host)
            VALUES (?,?,?,'room',?,'Коворкинг',?,?,'Выбери интервал до трёх часов. Комната целиком за твоей группой.','')
            ON CONFLICT DO NOTHING
        """, (bid, UNIVERSITY, teacher["id"], title, room, equipment_text))
        if key in activity_extensions.rooms():
            db.execute("INSERT INTO activity_room VALUES (?,?) ON CONFLICT DO NOTHING", (bid, key))
            db.execute("INSERT INTO campus_room VALUES (?,?,?) ON CONFLICT DO NOTHING", (bid, UNIVERSITY, room))
        for offset in range(15):
            day = today + timedelta(days=offset)
            slot_example(db, f"showcase:slot:{code}:{day}", bid, at(day, 9), at(day, 20), 1)
    first, last = at(today, 0), at(today + timedelta(days=7), 0)
    for index, (code, kind, *_) in enumerate(reservations.CATALOG + reservations.ACTIVITIES):
        offer = reservations.fixture_id(UNIVERSITY, code)
        count = db.execute("SELECT count(*) FROM reservation_slot WHERE bookable_id=? AND closed=0 "
                           "AND starts_at>=? AND starts_at<?", (offer, first, last)).fetchone()[0]
        if count >= 2:
            continue
        for offset in (2, 5):
            day = today + timedelta(days=offset)
            hour = 10 + index
            capacity = 1 if kind in {"room", "consultation"} or code == "sport-volley" else 4
            span = 4 if kind == "room" else 1
            slot_example(db, f"cabinet:slot:{code}:{day}", offer, at(day, hour), at(day, hour + span), capacity)


def availability(db, teacher, students, today):
    with write(db):
        db.execute("UPDATE university SET name='Учебный кампус' WHERE id=? AND name='СПбПУ'", (UNIVERSITY,))
        schedule_examples(db, students, today)
        booking_examples(db, teacher, today)


def application_examples(db, student, teacher):
    if not applications.documents(db, student):
        aid = applications.create_application(db, student, "enrolment-certificate",
                                              {"purpose": "Для учебной библиотеки", "delivery": "electronic"},
                                              seed_id("cabinet:certificate"))
        for status in ("review", "ready"):
            row = applications.applications(db, teacher, aid)
            if status in dict(applications.staff_actions(row)):
                applications.staff_update(db, teacher, aid, status, "", "", str(row["version"]),
                                          seed_id("cabinet:certificate:" + status))
    applications.create_application(db, student, "consultation",
                                    {"details": "Подскажите, где посмотреть даты консультаций перед зачётом."},
                                    seed_id("cabinet:consultation"))
    for service, values, stages in (
        ("academic-transcript", {"purpose": "Для учебного конкурса", "period": "current"},
         (("review", ""),)),
        ("room-repair", {"location": "Коворкинг, рабочее место у окна", "issue": "repair",
                         "details": "При подключении ноутбука розетка не работает."},
         (("review", ""), ("needs_changes", "Уточни номер места или приложи фотографию розетки."))),
        ("data-question", {"details": "Проверьте, пожалуйста, номер аудитории дополнительной консультации."},
         (("review", ""), ("completed", "Аудитория подтверждена в расписании консультаций."))),
    ):
        key = f"showcase:application:{service}"
        aid = applications.create_application(db, student, service, values, seed_id(key))
        for status, note in stages:
            action = key + ":" + status
            if not saved(db, teacher, "application.status", action):
                row = applications.applications(db, teacher, aid)
                if status in dict(applications.staff_actions(row)):
                    applications.staff_update(db, teacher, aid, status, note, "", str(row["version"]), seed_id(action))
    applications.create_application(db, student, "student-data", {"record": "program", "proposed": "Прикладная информатика"},
                                    seed_id("showcase:application:draft"), draft=True)


def energy_group_examples(db, student, teacher):
    document_key = seed_id("showcase:energy:certificate")
    aid = applications.create_application(db, student, "enrolment-certificate",
                                          {"purpose": "Учебный образец", "delivery": "electronic"}, document_key)
    announcement_id = seed_id("showcase:energy:announcement")
    title = "Консультация по лабораторным работам"
    body = "Учебное объявление. Вопросы по расчётам и сдаче лабораторных работ можно задать на консультации."
    stamp = timestamp()
    with write(db):
        # Import an already issued demo document without granting the demo operator another group.
        application = applications.applications(db, student, aid)
        if application["status"] == "submitted" and application["version"] == 1:
            applications.issue_document(db, teacher, application, stamp)
            db.execute("UPDATE application SET status='ready', updated_at=?, version=version+1 WHERE id=?",
                       (stamp, aid))
            applications.history_entry(db, teacher, aid, "ready", stamp, note="Учебный образец выдан.")
            services.notify(db, UNIVERSITY, student["id"], "application", aid, f"application:{aid}:ready:1",
                            "Заявка: документ готов", application["service_name"], stamp)
        inserted = db.execute("""
            INSERT INTO announcement (id, university_id, group_id, staff_id, title, body, created_at)
            VALUES (?, ?, 'demo-energy', ?, ?, ?, ?) ON CONFLICT(id) DO NOTHING
        """, (announcement_id, UNIVERSITY, teacher["id"], title, body, stamp)).rowcount
        if inserted:
            services.notify(db, UNIVERSITY, student["id"], "announcement", announcement_id,
                            f"announcement:{announcement_id}", title, "Учебное объявление", stamp)


def practice_example(db, student, teacher, today):
    key = "cabinet:practice"
    receipt = saved(db, student, "application.submit", key)
    if not receipt and practice.cases(db, student):
        return
    if receipt:
        aid = receipt["result_id"]
    else:
        options = application_forms.options(db, student, "practice")
        if not options:
            return
        aid = applications.create_application(db, student, "practice-enrolment", {
            "practice": next(iter(options)), "goal": "Подготовить учебную выборку и описать ход эксперимента.",
        }, seed_id(key))
    for status, note in (("review", ""), ("completed", "Учебная цель согласована.")):
        row = applications.applications(db, teacher, aid)
        if status in dict(applications.staff_actions(row)):
            applications.staff_update(db, teacher, aid, status, note, "", str(row["version"]),
                                      seed_id(key + ":" + status))
    case = next((item for item in practice.cases(db, student) if item["application_id"] == aid), None)
    if case and case["state"] == "active" and case["starts_at"] >= at(today, 0):
        practice.diary(db, student, case["id"], today.isoformat(), "Сверила план работы с руководителем.",
                       seed_id(key + ":diary"))


def book_sample(db, student):
    if saved(db, student, "reservation.book", "cabinet:booking") or reservations.bookings(db, student, kind="sport"):
        return
    offer = reservations.fixture_id(UNIVERSITY, "sport-stretch")
    for slot in reservations.slots(db, student, offer):
        if (not slot["closed"] and slot["starts_at"] > timestamp()
                and len(reservations.occupied(db, slot)) < slot["capacity"]
                and not reservations.bookings(db, student, active_only=True, start=slot["starts_at"], end=slot["ends_at"])):
            reservations.reserve(db, student, slot["id"], "", "", True, seed_id("cabinet:booking"), str(slot["version"]))
            return


def upcoming_room_sample(db, student, code, day, begin):
    key = f"showcase:booking:{code}:{day}:{begin}"
    if saved(db, student, "reservation.book", key):
        return
    start, end = at(day, begin), at(day, begin + 1)
    if start <= timestamp():
        return
    offer = demo_id(UNIVERSITY, "bookable", code)
    for slot in reservations.slots(db, student, offer):
        if not slot["closed"] and slot["starts_at"] <= start < end <= slot["ends_at"]:
            if (len(reservations.occupied(db, slot, start, end)) < slot["capacity"]
                    and not reservations.bookings(db, student, active_only=True, start=start, end=end)
                    and not reservations.lesson_conflict(db, student, start, end)):
                reservations.reserve(db, student, slot["id"], f"{day}T{begin:02}:00", f"{day}T{begin + 1:02}:00",
                                     False, seed_id(key), str(slot["version"]))
            return


def room_participants(db, students, today):
    for user, code, offset, hour in (
        ("student-a", "showcase-coworking", 2, 17),
        ("student-b", "showcase-coworking", 1, 16),
        ("student-b", "showcase-coworking", 2, 15),
        ("student-b", "showcase-meeting", 1, 13),
        ("student-a", "showcase-meeting", 3, 16),
    ):
        if user in students:
            upcoming_room_sample(db, students[user], code, today + timedelta(days=offset), hour)


def afisha_example(db, student):
    key = "showcase:activity:team-talk"
    if saved(db, student, "reservation.book", key):
        return
    offer = reservations.fixture_id(UNIVERSITY, "event-talk")
    for slot in reservations.slots(db, student, offer):
        start, end = slot["starts_at"], slot["ends_at"]
        if (not slot["closed"] and start > timestamp() and len(reservations.occupied(db, slot)) < slot["capacity"]
                and not reservations.bookings(db, student, active_only=True, start=start, end=end)
                and not reservations.lesson_conflict(db, student, start, end)):
            reservations.reserve(db, student, slot["id"], "", "", False, seed_id(key), str(slot["version"]))
            return


def book_equipment(db, student, today):
    key = "cabinet:equipment"
    if saved(db, student, "equipment.reserve", key) or equipment.loans(db, student):
        return
    day = today + timedelta(days=4)
    unit = next((row for row in equipment.units(db, student) if row["label"] == "П-01"), None)
    if unit and not unit["maintenance"] and not equipment.occupied(db, unit, at(day, 16), at(day, 18)):
        equipment.reserve(db, student, unit["id"], {
            "starts": f"{day}T16:00", "ends": f"{day}T18:00", "purpose": "Учебная защита проекта",
        }, seed_id(key))


def study_examples(db, student, teacher, today):
    sid = personal_study.create(db, student, "Проектная мастерская · демо", None, "", seed_id("cabinet:subject"))
    if db.execute("SELECT 1 FROM personal_subject WHERE id=? AND owner_id=?", (sid, student["id"])).fetchone():
        study_materials.add(db, student, sid, {
            "kind": "note", "title": "Идеи для учебного проекта",
            "value": "Выбрать показатель, собрать тестовую выборку и обсудить результаты на встрече команды.",
        }, None, seed_id("cabinet:material"))
    if not saved(db, student, "task.save", "cabinet:task"):
        workspace.save_task(db, student, {"title": "Собрать вопросы к семинару", "subject": "databases",
                            "due": f"{today + timedelta(days=3)}T17:00"}, seed_id("cabinet:task"))
    if not saved(db, student, "event.save", "cabinet:event"):
        day = today + timedelta(days=3)
        workspace.save_event(db, student, {"title": "Демо · встреча учебной команды", "location": "Кампус · переговорная",
                             "starts": f"{day}T17:30", "ends": f"{day}T18:15"}, seed_id("cabinet:event"))
    if not teams.lists(db, student):
        teams.create(db, student, "Учебная команда «Маяк»", seed_id("cabinet:team"))
    portfolio.save(db, student, {"kind": "project", "title": "Демо · карта студенческих маршрутов",
                                "content": "Прототип поиска аудиторий и переходов между корпусами."}, seed_id("cabinet:portfolio"))
    courses.course_action(db, student, demo_id(UNIVERSITY, "course", "short-study"), "favorite", seed_id("cabinet:favorite"))
    if not lending.loans(db, student):
        copy = next((row for row in lending.available_copies(db, teacher)
                     if row["label"].startswith("ДЕМО-DB-") and not row["reserved"]), None)
        if copy:
            lending.issue(db, teacher, student["id"], copy["id"], seed_id("cabinet:library:" + copy["id"]))


def personal_examples(db, student, today):
    group = student["group_id"]
    if group not in LESSONS:
        return
    subject, _, _, _ = LESSONS[group][0]
    primary = group == "demo-ai"
    due = (now().astimezone(ZONE) + timedelta(hours=18)).strftime("%Y-%m-%dT%H:%M")
    tasks = (
        ("summary", "Подготовить разбор алгоритма" if primary else "Оформить расчёт для лабораторной",
         "Записать решение и проверить результат на двух примерах.", "high", due),
        ("review", "Повторить графы и обходы" if primary else "Повторить правила Кирхгофа",
         "Отметить вопросы для консультации.", "normal", f"{today + timedelta(days=5)}T18:00"),
        ("send", "Собрать материалы по проекту", "Сохранить ссылки и короткие комментарии.", "low", ""),
    )
    subjects = {row["id"] for row in workspace.subjects(db, student)}
    for key, title, desc, priority, deadline in tasks:
        ref = f"showcase:task:{key}"
        if not saved(db, student, "task.save", ref):
            task_id = workspace.save_task(db, student, {
                "title": title, "description": desc, "priority": priority, "due": deadline,
                "subject": subject if subject in subjects else "",
            }, seed_id(ref))
            if key == "send":
                workspace.task_action(db, student, task_id, "complete", "1", seed_id(ref + ":done"))
    event = "showcase:event:group"
    if not saved(db, student, "event.save", event):
        day = today + timedelta(days=6)
        workspace.save_event(db, student, {"title": "Подготовка с одногруппниками", "location": "Учебный кампус",
                             "starts": f"{day}T16:00", "ends": f"{day}T17:30"}, seed_id(event))
    ref = "a1" if primary else "b1"
    lesson_id = seed_id(ref)
    if db.execute("SELECT 1 FROM schedule_event WHERE id=? AND group_id=? AND period_id=?",
                  (lesson_id, group, student["period_id"])).fetchone():
        workspace.write_note(db, student, seed_id(f"showcase:note:{ref}"),
                             "После занятия сверить примеры с конспектом и подготовить вопрос к следующей паре.",
                             kind="lesson", target=lesson_id)
    reminders.refresh(db, student)


def staff_examples(db, teacher, student, today):
    group = student["group_id"]
    for ref, title, body in (
        ("office", "Дополнительные консультации на этой неделе",
         "На консультации можно обсудить задачи, темы проектов и порядок сдачи работ. Время доступно в «Бронированиях»."),
        ("practice", "Открыта регистрация на проектную встречу",
         "Короткие выступления команд и вопросы к наставникам. Места можно занять в разделе «Афиша»."),
        ("library", "Учебные материалы доступны в библиотеке",
         "Новые экземпляры по базам данных и программированию можно найти в каталоге. В карточке книги доступна запись."),
    ):
        services.publish(db, teacher, group, title, body, seed_id("showcase:announcement:" + ref))
    key = "showcase:move"
    if saved(db, teacher, "schedule.move", key):
        return
    room = activity_extensions.rooms().get("b1-1015")
    lesson = db.execute("""
        SELECT id,starts_at,ends_at FROM schedule_event WHERE university_id=? AND group_id=?
        AND period_id=? AND version=1 AND source_id=? AND source_event_id LIKE 'showcase:lesson:%'
        AND starts_at>=? ORDER BY starts_at LIMIT 1
    """, (UNIVERSITY, group, student["period_id"], SOURCE, at(today + timedelta(days=1), 0))).fetchone()
    if room and lesson:
        from dean.schedule import local_value

        start, end = (local_value(lesson[field], ZONE.key).strftime("%Y-%m-%dT%H:%M") for field in ("starts_at", "ends_at"))
        services.move_lesson(db, teacher, lesson["id"], 1, start, end, room, seed_id(key))


def assignment_example(db, teacher, today):
    key = "cabinet:coursework"
    receipt = saved(db, teacher, "coursework.save", key)
    if not receipt and coursework.assignment(db, teacher):
        return
    scope = next((row for row in coursework.teacher_scopes(db, teacher)
                  if row["group_id"] == "demo-ai" and row["subject_id"] == "databases"), None)
    if scope:
        assignment = receipt["result_id"] if receipt else coursework.save_assignment(db, teacher, {
            "scope": "|".join(scope[name] for name in ("group_id", "period_id", "subject_id")),
            "title": "Демо · спроектировать схему каталога",
            "description": "Опиши три таблицы, первичные ключи и связи между таблицами.",
            "due": f"{today + timedelta(days=7)}T20:00", "response_mode": "text", "attempt_limit": "2",
            "rubric": "Схема таблиц | 6\nОбъяснение связей | 4",
        }, seed_id(key))
        if not saved(db, teacher, "coursework.publish", key + ":publish"):
            row = coursework.assignment(db, teacher, assignment)
            coursework.change_publication(db, teacher, assignment, "publish", str(row["version"]), seed_id(key + ":publish"))


def populate(db, teacher, students):
    today = now().astimezone(ZONE).date()
    availability(db, teacher, students, today)
    room_participants(db, students, today)
    student = students.get("student-a")
    groups = {row["id"] for row in services.staff_groups(db, teacher)}
    if student and student["group_id"] == "demo-ai" and student["group_id"] in groups:
        application_examples(db, student, teacher)
        practice_example(db, student, teacher, today)
        book_sample(db, student)
        afisha_example(db, student)
        book_equipment(db, student, today)
        study_examples(db, student, teacher, today)
        assignment_example(db, teacher, today)
        staff_examples(db, teacher, student, today)
    energy_student = students.get("student-b")
    if energy_student and energy_student["group_id"] == "demo-energy":
        energy_group_examples(db, energy_student, teacher)
    for owner in students.values():
        personal_examples(db, owner, today)
