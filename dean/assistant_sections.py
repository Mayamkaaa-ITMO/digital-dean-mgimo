"""Project knowledge and scoped readers shared by both assistant entry points."""
from . import (applications, campus_directory, campus_pay, coursework, curriculum, equipment, finances,
               individual_plan, lecture_studio, lending, library_queue, portfolio, practice, profile_tools, public_schedule,
               public_sync, reservations, retakes, room_occupancy, services, study_materials, teams)
from .schedule import local_value
from .domain import timestamp
import re


STUDENT = {
    "library": ("/library/mine", "Мои книги", "Срок возврата, продление, резервы и очередь. Для поиска книги открой каталог библиотеки."),
    "catalog": ("/library", "Каталог библиотеки", "Найди издание по автору или названию, посмотри экземпляры и доступность резерва."),
    "bookings": ("/bookings/mine", "Мои бронирования", "Записи на помещения, консультации, события и тренировки. В карточке доступны условия и отмена."),
    "reserve": ("/bookings", "Записаться или забронировать", "Выбери предложение, затем свободный интервал. Проверь время и подтверди запись."),
    "equipment": ("/equipment", "Техника кампуса", "Проверь доступные устройства, резерв и историю выдачи."),
    "retakes": ("/retakes", "Пересдачи", "Открой задолженность, проверь условия допуска и доступные окна."),
    "curriculum": ("/curriculum", "Учебный план", "Обязательные предметы и дисциплины по выбору по периодам."),
    "subjects": ("/study/subjects", "Мои предметы", "Открой предмет, чтобы увидеть его занятия, материалы и заметки."),
    "work": ("/work", "Учебные работы", "Задания преподавателя, сроки, отправка ответа и результат проверки."),
    "preparation": ("/study/preparation", "План подготовки", "Выбери срок, посмотри предложенные учебные блоки и подтверди подходящие."),
    "timer": ("/study/timer", "Учебный таймер", "Запусти учебную сессию и посмотри историю занятий."),
    "courses": ("/courses", "Демо-курсы", "Каталог учебных курсов с уроками и отметками прохождения."),
    "finance": ("/finance", "Финансы", "Начисления, стипендии, выплаты и сроки оплаты. Оплаты в кабинете демонстрационные."),
    "budget": ("/finance/budget", "Личный бюджет", "Доходы и расходы в демонстрационном бюджете."),
    "residence": ("/residence", "Проживание", "Сведения о месте в общежитии и связанных услугах."),
    "activities": ("/activities", "Афиша", "Выбери событие и доступный сеанс. Оформи запись и посмотри свой билет."),
    "teams": ("/teams", "Совместная учёба", "Учебные команды, приглашения и совместные материалы."),
    "clubs": ("/clubs", "Клубы", "Каталог студенческих объединений и участие в клубах."),
    "portfolio": ("/portfolio", "Портфолио", "Достижения, подтверждения и резюме."),
    "opportunities": ("/opportunities", "Практики и проекты", "Открой предложение и посмотри условия участия."),
    "practice": ("/practice", "Дневник практики", "Записи практики, отправка отчёта и замечания руководителя."),
    "notifications": ("/notifications", "Уведомления", "Изменения в расписании, ответы и результаты действий."),
    "support": ("/support", "Поддержка", "Создай обращение, проверь его статус и ответ в карточке."),
    "import": ("/calendar/import", "Импорт календаря", "Загрузи CSV и проверь предварительный просмотр."),
    "export": ("/calendar/export", "Экспорт календаря", "Скачай календарь или настрой подписку на расписание."),
    "context": ("/schedule/context", "Учебный контекст", "Выбери вуз и группу для просмотра расписания."),
    "free": ("/schedule/free", "Свободное время", "Посмотри свободные интервалы расписания."),
    "changes": ("/schedule/changes", "Изменения расписания", "Проверь переносы и обновления занятий."),
    "universities": ("/universities", "Каталог вузов", "Открытые университетские расписания, группы и аудитории."),
    "profile": ("/profile", "Профиль", "Настройки профиля, способы входа и персональные данные."),
    "privacy": ("/profile/privacy", "Приватность", "Проверь параметры доступа к своим данным."),
    "data": ("/profile/data", "Перенос данных", "Экспорт и восстановление личных данных через предварительный просмотр."),
    "search": ("/search", "Поиск", "Найди доступные материалы и записи кабинета по ключевым словам."),
}
STAFF = {
    "work": ("/staff/work", "Учебные работы", "Создание заданий и проверка работ по назначенным дисциплинам."),
    "library": ("/staff/library", "Выдача книг", "Выдача и возврат экземпляров в доступных группах."),
    "bookings": ("/staff/bookings", "Управление записями", "Слоты, участники, посещаемость и проверка билетов."),
    "equipment": ("/staff/equipment", "Выдача техники", "Управление своими устройствами, резервами и возвратами."),
    "finance": ("/staff/finance", "Назначения и выплаты", "Демонстрационные выплаты в закреплённых группах."),
    "practice": ("/staff/practice", "Руководство практикой", "Проверка дневника и отчёта назначенных студентов."),
    "achievements": ("/staff/achievements", "Подтверждение достижений", "Проверь присланные достижения."),
    "support": ("/staff/support", "Поддержка", "Открой обращение своей группы и ответь студенту."),
    "moderation": ("/staff/moderation", "Модерация", "Проверка материалов студенческих сообществ."),
}
FEATURES = (
    ("wallet", campus_pay, "Кампусный кошелёк", "Демо-покупки, баланс и история операций."),
    ("occupancy", room_occupancy, "Занятость аудиторий", "Демонстрационные данные о загрузке помещений."),
    ("directory", campus_directory, "Справочник кампуса", "Найди службу или место и перейди на карту."),
    ("studio", lecture_studio, "Лекционная студия", "Запись с таймкодами и подготовка черновика учебной работы."),
)


def sections(user, base, db=None):
    result = {**base, **(STUDENT if user["role"] == "student" else STAFF)}
    result["lecture_library"] = ("/lecture-library", "Видеотека", "Поиск по курсам, главы, субтитры, конспекты и задачи.")
    if db is not None and public_sync.operator(db, user):
        result["public"] = ("/staff/public", "Публичные источники", "Предпросмотр обновлений, публикация и откат данных стенда.")
    for key, module, title, description in FEATURES:
        if module.available(user):
            result[key] = (module.PATH, title, description)
    from . import campus_lab
    result.update(campus_lab.assistant_pages(user))
    return result


SHARED_READ = {"library", "equipment", "retakes", "finance", "practice", "support", "work"}
STUDENT_READ = {"grades", "bookings", "documents", "portfolio", "curriculum", "catalog", "notifications", "teams"}


def readable(user):
    from . import campus_lab
    base = SHARED_READ | STUDENT_READ if user["role"] == "student" else SHARED_READ
    return base | {"lecture_library"} | campus_lab.assistant_pages(user).keys()


def material_passages(db, user, query):
    words = {w[:5] for w in re.findall(r"[а-яёa-z0-9]+", query.casefold()) if len(w) > 2 and w not in {"как", "для", "при", "мне", "что", "перед"}}
    if not words:
        return []
    matches = []
    for row in study_materials.search_rows(db, user):
        content = (row["value"] if row["kind"] == "note" else
                   (row["text_excerpt"] or b"").decode("utf-8", errors="replace"))
        title = row["title"] + " · " + row["subject"]
        hits = sum(w in (title + " " + content).casefold() for w in words)
        if not content or hits < min(2, len(words)):
            continue
        score = hits + sum(w in title.casefold() for w in words)
        offset = min((content.casefold().find(w) for w in words if w in content.casefold()), default=0)
        start = max(0, offset - 240)
        matches.append((score, title, content[start:start + 1600], "/study/subjects/" + row["subject_id"] + "/materials"))
    return sorted(matches, key=lambda m: (-m[0], m[1], m[3]))[:3]


def linked_records(db, user, topic, question, previous):
    if topic == "bookings":
        rows = [r for r in reservations.bookings(db, user, active_only=True) if r["ends_at"] > timestamp()]
        if any(word in question.casefold() for word in ("ближайш", "оконч", "заканч", "до скольких")):
            rows = rows[:1]
        return [[f'/bookings/{r["id"]}', f'{r["title"]} · начало {local_value(r["starts_at"], user["timezone"]):%d.%m %H:%M}, окончание {local_value(r["ends_at"], user["timezone"]):%d.%m %H:%M}'] for r in rows[:6]]
    rows = applications.documents(db, user)
    for query in (question, previous):
        matches = [r for r in rows if (r["purpose"] and r["purpose"].casefold() in query.casefold())
                   or r["application_id"][:8] in query.casefold()]
        if matches:
            rows = matches
            break
    return [[f'/documents/{r["id"]}', f'{r["title"]} № {r["application_id"][:8]} · {r["purpose"] or "цель не указана"} · {applications.STATUSES[r["status"]]}'] for r in rows[:6]]


def match_grade_subject(rows, question):
    def words(text):
        return re.findall(r"[а-яa-z0-9]+", text.casefold().replace("ё", "е"))

    tokens = set(words(question))
    matches = []
    for row in rows:
        subject = [word for word in words(row["subject"]) if len(word) > 1]
        if subject and all(any(candidate == word or (
            len(word) >= 4 and candidate.startswith(word[:-2]) and len(candidate) >= len(word) - 1
        ) for candidate in tokens) for word in subject):
            matches.append(row)
    return matches[0] if len(matches) == 1 else None


def exam_dates(db, user, question, include_grades=False):
    rows = services.results(db, user)
    subject = match_grade_subject(rows, question)
    result = grade_details(db, user, subject, include_date=False) if include_grades and subject is not None else None
    blocks = [result["text"]] if result else []
    links = result["section_links"] if result else []
    if include_grades and not result:
        grades = records(db, user, "grades")
        blocks.append("Успеваемость\n" + ("\n".join("• " + row for row in grades[:6]) or "Записей нет."))
        links.append(["/grades", "Успеваемость"])

    exams = [row for row in ([subject] if subject is not None else rows) if row["kind"] == "exam" and row["exam_at"]]
    exams.sort(key=lambda row: row["exam_at"])
    upcoming = [row for row in exams if row["exam_at"] >= timestamp() and row["outcome"] is None]
    if subject is not None:
        empty = f'Дата экзамена по дисциплине «{subject["subject"]}» пока не указана.'
    else:
        empty = "В этом семестре даты экзаменов пока не указаны."
    if exams:
        chosen = upcoming or exams
        if not upcoming:
            blocks.append("Предстоящих экзаменов с указанной датой нет. Даты из ведомости:")
        for row in chosen[:6]:
            when = local_value(row["exam_at"], user["timezone"])
            blocks.append(f'{row["subject"]} · экзамен {when:%d.%m.%Y в %H:%M}.')
            link = f'/grades/{row["id"]}'
            if all(href != link for href, _ in links):
                links.append([link, row["subject"]])
    else:
        blocks.append(empty)
        if not links:
            links.append(["/grades", "Сессия и задолженности"])
    return {"text": "\n\n".join(blocks), "link": links[0][0], "section_links": links}


def grade_details(db, user, row, *, include_date=True):
    from . import learning
    from .reservation_views import retake_admission
    from .views import grade_text

    progress = learning.grade_progress(row, learning.assessments(db, row["id"]))
    label = grade_text(row, progress)
    state = learning.STATES[progress]
    details = " · ".join(dict.fromkeys((row["subject"], label, state)))
    if include_date and row["exam_at"] and progress == "scheduled":
        when = local_value(row["exam_at"], user["timezone"])
        details += f"\nЭкзамен {when:%d.%m.%Y в %H:%M}." if row["kind"] == "exam" else f"\nЗачёт {when:%d.%m.%Y в %H:%M}."
    link = f'/grades/{row["id"]}'
    links = [[link, "Открыть результат по дисциплине"]]
    debt = next((item for item in retakes.debts(db, user) if item["id"] == row["id"]), None)
    if debt is not None:
        admission, next_step = retake_admission(debt)
        details += f"\n{admission}. {next_step}"
        links.append([f'/retakes/{debt["id"]}', "Условия допуска и пересдача"])
    return {"text": details, "link": link, "section_links": links}


def grade_result(db, user, question):
    row = match_grade_subject(services.results(db, user), question)
    return grade_details(db, user, row) if row is not None else None


def records(db, user, topic):
    staff = user["role"] == "staff"
    def when(value):
        return local_value(value, user["timezone"]).strftime("%d.%m.%Y %H:%M")

    from . import campus_lab
    if topic in campus_lab.assistant_pages(user):
        return campus_lab.assistant_records(db,user,topic)
    if topic not in readable(user):
        return []
    if topic == "lecture_library":
        from . import lecture_library
        return [f"{course['title']} · {course['author']} · {course['subject']}"
                for course in lecture_library.catalog_data().values()]
    if topic == "library":
        rows = [r["title"] + (" · возвращена" if r["returned_at"] else " · вернуть до " + when(r["due_at"]))
                for r in lending.loans(db, user, manage=staff)]
        if not staff:
            rows.extend(r["title"] + " · " + library_queue.STATES[r["state"]] for r in library_queue.holds(db, user))
        return rows
    if topic == "equipment":
        return [r["title"] + " · " + equipment.STATES[r["state"]] for r in equipment.loans(db, user, manage=staff)]
    if topic == "retakes":
        return [r["subject"] + (" · закрыта" if r["closed_at"] else " · " + r["reason"] + ". " + r["conditions"])
                for r in retakes.debts(db, user, manage=staff)]
    if topic == "finance":
        rows = [r["title"] + (" · выплачено" if r["paid_at"] else " · назначено")
                for r in finances.awards(db, user, manage=staff)]
        if not staff:
            rows.extend(r["title"] + (" · оплачено" if r["paid_at"] else " · оплатить до " + when(r["due_at"]))
                        for r in finances.charges(db, user))
        return rows
    if topic == "practice":
        return [r["title"] + " · " + practice.STATES[r["state"]] for r in practice.cases(db, user, manage=staff)]
    if topic == "support":
        labels = {"open":"открыто", "review":"на рассмотрении", "closed":"закрыто"}
        return [r["title"] + " · " + labels[r["state"]]
                for r in profile_tools.tickets(db, user)]
    if topic == "grades":
        from .views import grade_text
        return [r["subject"] + " · " + grade_text(r) for r in services.results(db, user)]
    if topic == "bookings":
        return [r["title"] + " · " + when(r["starts_at"]) for r in reservations.bookings(db, user, active_only=True)]
    if topic == "documents":
        return [r["title"] + " · выдан " + when(r["issued_at"]) for r in applications.documents(db, user)]
    if topic == "portfolio":
        return [r["title"] + (" · подтверждено" if r["verified_at"] else "") for r in portfolio.entries(db, user)]
    if topic == "curriculum":
        rows = [f'{r["period"]} · {r["title"]} · {r["credits"]} кр. · '
                + ("обязательная" if r["kind"] == "required" else "по выбору")
                for r in curriculum.items(db, user)]
        for block in individual_plan.blocks(db, user):
            if block["rule"] == "credits":
                rows.append(f'{block["title"]} · нужно выбрать ровно {block["credit_target"]} кр.')
                for r in individual_plan.courses(db, user, block):
                    admission = "требование выполнено" if individual_plan.eligible(db, user, r) else "требование не выполнено"
                    rows.append(f'{block["title"]} · {r["title"]} · {r["credits"]} кр. · '
                                f'мест {r["capacity"] - r["enrolled"]} · {admission}')
        return rows
    if topic == "catalog":
        return [f'{r["title"]} · {r["author"]} · {r["topic"]} · '
                f'свободно {r["copies"] - r["lent"] - r["reserved"]} из {r["copies"]}'
                for r in lending.books(db, user)]
    if topic == "work":
        if staff:
            return [r["title"] + " · " + coursework.STATES[r["state"]] for r in coursework.assignment(db, user)]
        return [r["title"] + " · " + coursework.WORK_STATES[r["status"]] for r in coursework.student_list(db, user)]
    if topic == "notifications":
        return [r["title"] for r in services.notifications(db, user, unread=True, limit=20,
                                                          selection=public_schedule.context(db, user))]
    return [r["title"] for r in teams.lists(db, user)]
