import json
from datetime import date

from .domain import DomainError, field

PERIODS = {"current": "Текущий семестр", "all": "Все доступные семестры"}
PICKUP = {"desk": "ДЕМО · учебная часть А-104", "service": "ДЕМО · окно сервисов Б-105"}
CATEGORIES = {
    "study": "Учебные вопросы", "documents": "Документы", "support": "Поддержка",
    "campus": "Кампус и проживание", "practice": "Практика и клубы",
}


def text(key, label, limit=300, minimum=1, kind="text"):
    return {"key": key, "label": label, "kind": kind, "limit": limit, "minimum": minimum}


def select(key, label, choices, default=""):
    return {"key": key, "label": label, "kind": "select", "choices": choices, "default": default}


def reference(key, label, source, required=True):
    return {"key": key, "label": label, "kind": "reference", "source": source, "required": required}


SUBJECT = reference("subject", "Дисциплина", "subject")
REASON = text("details", "Обоснование", 1000, 5, "textarea")
EVIDENCE = tuple(reference(key, label, "achievement", False) for key, label in (
    ("achievement", "Достижение"), ("achievement_2", "Второе достижение"), ("achievement_3", "Третье достижение"),
))

TEMPLATES = {
    "credit-transfer": ("study", "Перезачёт дисциплины", "Рассмотрение ранее освоенной дисциплины", 5,
        (SUBJECT, text("source_course", "Ранее освоенный курс"), text("hours", "Количество часов", 4, 1, "integer"),
         REASON), "Рекомендация по перезачёту сохранена. Баллы и ведомость изменяются отдельной операцией.", True),
    "material-aid": ("support", "Материальная помощь", "Заявка на демонстрационное назначение", 4,
        (select("basis", "Основание", {"study": "Учебные расходы", "housing": "Проживание", "other": "Другое"}),
         REASON, *EVIDENCE), "Одобрено демонстрационное назначение. Статус выплаты ведётся отдельно.", False),
    "early-assessment": ("study", "Досрочная аттестация", "Предложение даты досрочной сдачи", 3,
        (SUBJECT, text("requested_date", "Предлагаемая дата", 10, 1, "date"), REASON),
        "Запрос на досрочную дату согласован в демо. Официальное время в ведомости меняет сотрудник.", False),
    "assessment-extension": ("study", "Продление аттестации", "Запрос нового срока сдачи", 3,
        (SUBJECT, text("requested_date", "Новый срок", 10, 1, "date"), REASON),
        "Демонстрационное продление согласовано. Решение и причина сохранены в карточке.", False),
    "student-data": ("study", "Изменение учебных данных", "Запрос на проверку записи профиля", 4,
        (select("record", "Поле для проверки", {"name": "Отображаемое имя", "group": "Группа", "program": "Программа"}),
         text("proposed", "Предлагаемое значение"), REASON),
        "Уточнение принято к учёту в демо. Права и принадлежность аккаунта сохранены.", False),
    "academic-leave": ("study", "Академический отпуск", "Демонстрационное рассмотрение периода", 5,
        (text("from_date", "Начало периода", 10, 1, "date"), text("to_date", "Конец периода", 10, 1, "date"), REASON),
        "Решение об отпуске записано в демо. Аккаунт и текущий статус обучения сохранены.", False),
    "travel-support": ("support", "Поддержка поездки", "Заявка на поддержку учебной поездки", 5,
        (text("destination", "Место поездки"), text("requested_date", "Дата поездки", 10, 1, "date"),
         reference("activity", "Связанное мероприятие", "activity", False), REASON, *EVIDENCE),
        "Поддержка поездки согласована в демо. Объём поддержки указан в решении сотрудника.", False),
    "room-repair": ("campus", "Ремонт и бытовое обращение", "Сообщить о неисправности в демо-помещении", 2,
        (reference("room", "Помещение", "room", False), text("location", "Другое место или номер комнаты", 120, 0),
         select("issue", "Вид обращения", {"repair": "Ремонт", "cleaning": "Уборка", "other": "Бытовой вопрос"}),
         text("details", "Что случилось", 1000, 5, "textarea")),
        "Сотрудник подтвердил обработку обращения. Ход работ указан в решении.", False),
    "individual-plan": ("study", "Индивидуальный учебный план", "Передать снимок личного выбора рецензенту", 5,
        (text("details", "Цель индивидуального плана", 1000, 5, "textarea"),),
        "Индивидуальный учебный план утверждён. Утверждённая версия доступна в учебном плане.", False),
    "enhanced-stipend": ("support", "Повышенная стипендия", "Рассмотрение достижений для демо-назначения", 5,
        (reference("achievement", "Основное достижение", "achievement"), *EVIDENCE[1:], REASON),
        "Создано назначение повышенной стипендии. Выплата учитывается отдельно.", False),
    "charge-question": ("support", "Вопрос по начислению", "Уточнение начисления в демонстрационном учёте", 3,
        (reference("charge", "Начисление", "charge"), text("details", "Что нужно уточнить", 1000, 5, "textarea")),
        "Ответ по начислению сохранён. Сумма и статус оплаты ведутся в финансовом разделе.", False),
    "residence-request": ("campus", "Вопрос по проживанию", "Демонстрационное обращение по своей комнате", 3,
        (reference("residence", "Проживание", "residence"),
         select("issue", "Тема", {"repair": "Неисправность", "rules": "Правила", "period": "Срок проживания"}),
         text("details", "Суть вопроса", 1000, 5, "textarea")),
        "Сотрудник рассмотрел обращение по проживанию. Решение сохранено в истории.", False),
    "club-join": ("practice", "Вступление в клуб", "Заявка на участие в демонстрационном клубе", 3,
        (reference("club", "Клуб", "club"), text("details", "Почему хочешь вступить", 1000, 5, "textarea")),
        "Заявка одобрена. Обсуждения и события клуба доступны в разделе «Совместная учёба».", False),
    "practice-enrolment": ("practice", "Запись на учебную практику", "Назначение руководителя и срока отчёта", 4,
        (reference("practice", "Предложение", "practice"),
         text("goal", "Что хочешь сделать на практике", 700, 5, "textarea"),
         reference("achievement", "Связанное достижение", "achievement", False)),
        "Практика назначена. Дневник, сроки и отчёт находятся в карточке практики.", False),
}


def seed(db, university, version):
    db.executemany("""
        INSERT INTO service (university_id,id,name,description,placeholder,category,target_days)
        VALUES (?, ?, ?, ?, '', ?, ?) ON CONFLICT(university_id,id) DO NOTHING
    """, [(university, code, title, desc, category, days)
          for code, (category, title, desc, days, *_rest) in TEMPLATES.items()
          if version >= INTRODUCED.get(code, 8)])


INTRODUCED = {"individual-plan": 9, "enhanced-stipend": 10, "charge-question": 10, "residence-request": 10,
              "club-join": 12, "practice-enrolment": 13}


def definition(service):
    if "form_json" in service.keys() and service["form_json"]:
        stored = json.loads(service["form_json"])
        if stored:
            return stored
    keys = service.keys()
    sid = service["service_id"] if "service_id" in keys else service["id"]
    name = service["service_name"] if "service_name" in keys else service["name"]
    kind = service["document_kind"]
    version, result, required_file = 1, "", False
    if sid in TEMPLATES:
        _category, _title, _desc, _days, fields, result, required_file = TEMPLATES[sid]
        version = 2
    elif kind:
        fields = [text("purpose", "Цель получения", 200, 3)]
        if kind == "transcript":
            fields.append(select("period", "Период выписки", PERIODS, "current"))
        fields += [select("delivery", "Вид получения", {"electronic": "Электронное", "paper": "Бумажное (демо)"}, "electronic"),
                   select("copies", "Число экземпляров", {"1": "1", "2": "2", "3": "3"}, "1"),
                   select("pickup", "Место выдачи бумажной справки", {"": "Не выбрано", **PICKUP})]
        version = 2
    else:
        fields = [text("details", "Описание", 1000, 5, "textarea")]
    return {"version": version, "service": sid, "name": name, "target_days": service["target_days"],
            "document_kind": kind, "fields": fields, "result": result, "required_file": required_file}


def options(db, user, source):
    university, owner = user["university_id"], user["id"]
    if source == "subject":
        rows = db.execute("""
            SELECT DISTINCT s.id,s.name FROM subject s JOIN learning_result r
            ON r.subject_id=s.id AND r.university_id=s.university_id
            WHERE s.university_id=? AND r.student_id=? ORDER BY s.name
        """, (university, owner))
    elif source == "achievement":
        rows = db.execute("""
            SELECT id,title FROM portfolio_entry WHERE university_id=? AND student_id=? AND kind='achievement'
            ORDER BY created_at DESC LIMIT 100
        """, (university, owner))
    elif source == "room":
        rows = db.execute("SELECT id,title FROM bookable WHERE university_id=? AND kind='room' ORDER BY title",
                          (university,))
    elif source == "activity":
        rows = db.execute("""
            SELECT s.id,b.title || ' · ' || substr(s.starts_at,1,10)
            FROM reservation_slot s JOIN bookable b ON b.university_id=s.university_id AND b.id=s.bookable_id
            WHERE s.university_id=? AND b.kind='activity' ORDER BY s.starts_at DESC LIMIT 100
        """, (university,))
    elif source == "charge":
        rows = db.execute("""
            SELECT id,title || ' · ' || period FROM tuition_charge WHERE university_id=? AND student_id=?
            ORDER BY due_at DESC
        """, (university, owner))
    elif source == "residence":
        rows = db.execute("""
            SELECT r.id,h.title || ' · ' || r.room FROM residence_stay r
            JOIN residence_hall h ON h.id=r.hall_id AND h.university_id=r.university_id
            WHERE r.university_id=? AND r.student_id=? ORDER BY r.starts_on DESC
        """, (university, owner))
    elif source == "club":
        rows = db.execute("SELECT id,title FROM club WHERE university_id=? ORDER BY title", (university,))
    elif source == "practice":
        from .domain import timestamp
        rows = db.execute("""
            SELECT p.id,o.title FROM practice_offer p JOIN opportunity o ON o.id=p.id AND o.university_id=p.university_id
            WHERE p.university_id=? AND p.closes_at>? ORDER BY o.title
        """, (university, timestamp()))
    else:
        raise ValueError("Unknown form reference")
    return dict(rows)


def validate(db, user, schema, submitted, required):
    answers, links = {}, {}
    for spec in schema["fields"]:
        key, kind, label = spec["key"], spec["kind"], spec["label"]
        value = submitted.get(key, spec.get("default", ""))
        if kind == "reference":
            if value:
                title = options(db, user, spec["source"]).get(value)
                if title is None:
                    raise DomainError(422, f"Выбранное значение поля «{label}» недоступно.")
                snapshot = {"id": value, "title": title, "kind": spec["source"]}
                if spec["source"] == "achievement" and db.execute("PRAGMA user_version").fetchone()[0] >= 13:
                    evidence = db.execute("SELECT verified_at FROM portfolio_verification WHERE entry_id=?", (value,)).fetchone()
                    snapshot["verified_at"] = evidence[0] if evidence else None
                links[key] = snapshot
            elif required and spec["required"]:
                raise DomainError(422, f"Выбери «{label}».")
        elif kind == "select":
            if value not in spec["choices"] and (value or required):
                raise DomainError(422, f"Укажи «{label}».")
        else:
            value = field(value, label, spec["limit"], spec["minimum"] if required else 0)
            if value and kind == "date":
                try:
                    value = date.fromisoformat(value).isoformat()
                except ValueError:
                    raise DomainError(422, f"Укажи дату в поле «{label}».") from None
            if value and kind == "integer" and (not value.isascii() or not value.isdecimal() or int(value) < 1):
                raise DomainError(422, f"В поле «{label}» нужно положительное целое число.")
        answers[key] = value
    if answers.get("delivery") == "electronic":
        answers.update(copies="1", pickup="")
    elif required and answers.get("delivery") == "paper" and not answers.get("pickup"):
        raise DomainError(422, "Выбери место выдачи бумажного документа.")
    if answers.get("from_date") and answers.get("to_date") and answers["from_date"] > answers["to_date"]:
        raise DomainError(422, "Конец периода должен быть не раньше начала.")
    if required and schema["service"] == "room-repair" and not (answers["room"] or answers["location"]):
        raise DomainError(422, "Выбери помещение или уточни место обращения.")
    if links:
        answers["_links"] = links
    if schema["service"] == "individual-plan":
        from .individual_plan import snapshot
        answers["_plan"] = snapshot(db, user, required=required)
    return answers
