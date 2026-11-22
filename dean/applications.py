import json
from datetime import datetime, timedelta
from uuid import UUID

from . import application_forms as forms
from .application_forms import CATEGORIES, PERIODS
from .domain import DomainError, field, identifier, require, role, timestamp
from .pdf import render_document
from .schedule import local_value
from .services import command, notify

STATUSES = {
    "draft": "Черновик", "submitted": "Отправлена", "review": "Рассматривается",
    "needs_changes": "Требуется исправление", "resubmitted": "Повторно отправлена",
    "completed": "Выполнена", "ready": "Документ готов", "received": "Документ получен",
    "refused": "Отказано", "cancelled": "Отменена",
}
ACTIVE = {"submitted", "review", "needs_changes", "resubmitted"}
EDITABLE = {"draft", "needs_changes"}
CANCELLABLE = ACTIVE | {"draft"}
SELECT = """
    SELECT a.*, COALESCE(json_extract(a.form_json,'$.name'),s.name) AS service_name,
           CASE WHEN a.form_json='{}' THEN s.document_kind ELSE json_extract(a.form_json,'$.document_kind') END AS document_kind,
           COALESCE(json_extract(a.form_json,'$.target_days'),s.target_days) AS target_days,
           u.full_name AS student_name, g.name AS group_name, p.name AS program_name,
           p.faculty, g.course_year, u.period_id, t.label AS period_name,
           owner.full_name AS assignee_name, d.id AS document_id
    FROM application a
    JOIN service s ON s.university_id = a.university_id AND s.id = a.service_id
    JOIN account u ON u.university_id = a.university_id AND u.id = a.student_id
    JOIN study_group g ON g.university_id = a.university_id AND g.id = a.group_id
    JOIN program p ON p.university_id = g.university_id AND p.id = g.program_id
    JOIN academic_period t ON t.university_id = u.university_id AND t.id = u.period_id
    LEFT JOIN account owner ON owner.university_id = a.university_id AND owner.id = a.assignee_id
    LEFT JOIN document d ON d.university_id = a.university_id AND d.application_id = a.id
    WHERE a.university_id = ?
"""


def seed_catalog(db, university_id):
    db.executemany("""
        INSERT INTO service (university_id, id, name, description, placeholder, category, target_days, document_kind)
        VALUES (?, ?, ?, ?, ?, 'documents', ?, ?)
        ON CONFLICT(university_id, id) DO NOTHING
    """, [
        (university_id, "enrolment-certificate", "Справка об обучении",
         "Электронная справка с данными демонстрационного профиля.",
         "Например, для предоставления по месту требования.", 2, "enrolment"),
        (university_id, "academic-transcript", "Выписка об успеваемости",
         "Электронная выписка по выбранным демонстрационным учебным периодам.",
         "Например, для личного архива.", 3, "transcript"),
    ])


def catalog(db, user, service_id=None, *, q="", category=""):
    query = """SELECT s.*,m.form_json,m.department FROM service s
        LEFT JOIN managed_service m ON m.university_id=s.university_id AND m.service_id=s.id
        WHERE s.university_id=?"""
    params = (user["university_id"],)
    if user["role"] == "student":
        query += " AND (m.service_id IS NULL OR (m.group_id=? AND m.active=1))"
        params += (user["group_id"],)
    else:
        role(user,"staff")
        query += """ AND (m.service_id IS NULL OR EXISTS (
                SELECT 1 FROM staff_group sg WHERE sg.university_id=m.university_id AND sg.group_id=m.group_id AND sg.staff_id=?))"""
        params += (user["id"],)
    if service_id is not None:
        role(user, "student")
        return require(db.execute(query + " AND s.id=?", (*params, service_id)).fetchone())
    if category in CATEGORIES:
        query += " AND s.category=?"
        params += (category,)
    query += " ORDER BY s.category,s.name"
    rows = db.execute(query, params)
    term = q.strip().casefold()[:120]
    return [row for row in rows if term in (row["name"] + " " + row["description"]).casefold()]


def applications(db, user, application_id=None, *, status="", service_id=""):
    params = (user["university_id"], user["id"])
    if user["role"] == "staff":
        access = """ AND a.status <> 'draft' AND (a.status <> 'cancelled' OR EXISTS (
            SELECT 1 FROM application_history h WHERE h.application_id = a.id AND h.status = 'submitted'
        )) AND EXISTS (
            SELECT 1 FROM staff_group g
            WHERE g.university_id = a.university_id AND g.group_id = a.group_id AND g.staff_id = ?
        )"""
    else:
        role(user, "student")
        access = " AND a.student_id = ?"
    query = SELECT + access
    if application_id is not None:
        return require(db.execute(query + " AND a.id = ?", (*params, application_id)).fetchone())
    if status in STATUSES:
        query += " AND a.status = ?"
        params += (status,)
    if service_id:
        query += " AND a.service_id = ?"
        params += (service_id,)
    return list(db.execute(query + " ORDER BY a.created_at DESC, a.rowid DESC", params))


def application_details(db, user, application_id):
    item = applications(db, user, application_id)
    history = list(db.execute("""
        SELECT h.status, h.note, h.created_at, h.event_kind, u.full_name AS actor
        FROM application_history h JOIN account u ON u.id = h.actor_id
        WHERE h.application_id = ? AND (?=0 OR h.event_kind<>'saved') ORDER BY h.id
    """, (application_id, user["role"] == "staff")))
    return item, history


def answers(row):
    values = json.loads(row["answers_json"])
    if not values and row["details"]:
        return {"details": row["details"]}
    return values


def encode(values):
    return json.dumps(values, ensure_ascii=False, sort_keys=True)


def submissions(db, aid):
    return list(db.execute("""
        SELECT * FROM application_submission WHERE application_id=? ORDER BY rowid
    """, (aid,)))


def visible_answers(db, user, row, history):
    if user["role"] == "staff" and history:
        return json.loads(history[-1]["answers_json"])
    if user["role"] == "staff" and row["status"] == "needs_changes":
        previous = db.execute("""
            SELECT answers_json FROM application_history WHERE application_id=? AND status IN ('submitted','resubmitted')
            AND event_kind='status' AND answers_json IS NOT NULL ORDER BY id DESC LIMIT 1
        """, (row["id"],)).fetchone()
        return json.loads(previous[0]) if previous else {}
    return answers(row)


def save_submission(db, aid, version, schema, values, now):
    sid = identifier()
    db.execute("INSERT INTO application_submission VALUES (?, ?, ?, ?, ?, ?)",
               (sid, aid, version, encode(schema), encode(values), now))
    db.execute("""
        INSERT INTO application_submission_file SELECT ?,id FROM application_attachment
        WHERE application_id=? AND active=1
    """, (sid, aid))


def require_attachment(db, aid, schema):
    if schema["required_file"] and not db.execute("""
        SELECT 1 FROM application_attachment WHERE application_id=? AND active=1
    """, (aid,)).fetchone():
        raise DomainError(422, "Для подачи приложи подтверждение в PNG или TXT. Сначала сохрани черновик.")


def application_fingerprint(schema, values):
    if schema["service"] == "consultation":
        return values["details"]
    result = {k: v for k, v in values.items() if not k.startswith("_")}
    if result.get("delivery") == "electronic":
        for key in ("delivery", "copies", "pickup"):
            del result[key]
    return result


def history_entry(db, user, aid, status, now, *, kind="status", note="", values=None):
    db.execute("""
        INSERT INTO application_history (application_id, actor_id, status, note, created_at, event_kind, answers_json)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    """, (aid, user["id"], status, note, now, kind, encode(values) if values is not None else None))


def deadline(now, days):
    return timestamp(datetime.fromisoformat(now) + timedelta(days=days))


def creation_receipt(db, user, service_id, key, *, draft=False):
    role(user, "student")
    try:
        key = UUID(key).hex
    except (TypeError, ValueError):
        return None
    operation = "application.draft" if draft else "application.submit"
    row = db.execute("""
        SELECT a.id FROM command c JOIN application a
          ON a.id=c.result_id AND a.university_id=c.university_id AND a.student_id=c.actor_id
        WHERE c.university_id=? AND c.actor_id=? AND c.operation=? AND c.request_id=? AND a.service_id=?
    """, (user["university_id"], user["id"], operation, key, service_id)).fetchone()
    return row["id"] if row else None


def create_application(db, user, service_id, inputs, key, *, draft=False):
    service = catalog(db, user, service_id)
    schema = forms.definition(service)
    if service["form_json"] and inputs.get("form_version")!=str(schema["version"]):
        raise DomainError(409,"Форма услуги изменилась. Открой действующую версию.")
    values = forms.validate(db, user, schema, inputs, required=False)
    if not draft and schema["required_file"]:
        raise DomainError(422, "Сохрани черновик и приложи подтверждение, затем отправь заявление.")
    status = "draft" if draft else "submitted"
    fingerprint = application_fingerprint(schema, values)

    def apply():
        if forms.definition(catalog(db,user,service_id))!=schema:
            raise DomainError(409,"Форма услуги изменилась. Открой действующую версию.")
        aid, now = identifier(), timestamp()
        current = forms.validate(db, user, schema, inputs, required=not draft)
        db.execute("""
            INSERT INTO application
                (id, university_id, student_id, group_id, service_id, details, status, created_at, updated_at,
                 answers_json, due_at, form_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (aid, user["university_id"], user["id"], user["group_id"], service_id,
              current.get("details", ""), status, now, now, encode(current),
              None if draft else deadline(now, schema["target_days"]), encode(schema)))
        history_entry(db, user, aid, status, now, values=current)
        if not draft:
            save_submission(db, aid, 1, schema, current, now)
            notify(db, user["university_id"], user["id"], "application", aid, f"application:{aid}:submitted",
                   "Заявка отправлена", schema["name"], now)
        return aid

    operation = "application.draft" if draft else "application.submit"
    return command(db, user, operation, key, [service_id, fingerprint], apply)


def version_check(record, version):
    if version != str(record["version"]):
        raise DomainError(409, "Запись уже изменена. Открой актуальную карточку и повтори действие.")


def update_application(db, user, aid, action, inputs, version, key):
    role(user, "student")
    item = applications(db, user, aid)
    if action not in {"save", "send", "cancel", "receive"}:
        raise DomainError(422, "Выбери доступное действие.")
    schema = forms.definition(item)
    is_edit = action in {"save", "send"}
    payload = {key: inputs.get(key, "") for key in sorted(inputs) if key in {f["key"] for f in schema["fields"]}}

    def apply():
        current = applications(db, user, aid)
        version_check(current, version)
        status, due = current["status"], current["due_at"]
        active = status in EDITABLE
        values = forms.validate(db, user, schema, inputs, required=action == "send") if is_edit and active else None
        if action == "save" and active:
            new_status = status
        elif action == "send" and active:
            new_status = "submitted" if status == "draft" else "resubmitted"
        elif action == "cancel" and status in CANCELLABLE:
            new_status = "cancelled"
        elif action == "receive" and status == "ready" and (
                current["document_id"] or answers(current).get("delivery") == "paper"):
            new_status = "received"
        else:
            raise DomainError(409, "Действие недоступно для текущего статуса заявки.")
        now = timestamp()
        if action == "send":
            require_attachment(db, aid, schema)
            due = deadline(now, current["target_days"])
            save_submission(db, aid, current["version"] + 1, schema, values, now)
        db.execute("""
            UPDATE application SET status = ?, details = COALESCE(?, details), updated_at = ?,
                answers_json = COALESCE(?, answers_json), due_at = ?, version = version + 1 WHERE id = ?
        """, (new_status, values.get("details", "") if values is not None else None, now,
              encode(values) if values is not None else None, due, aid))
        history_entry(db, user, aid, new_status, now, kind="saved" if action == "save" else "status", values=values)
        if action == "send":
            notify(db, user["university_id"], user["id"], "application", aid,
                   f"application:{aid}:sent:{current['version']}", f"Заявка: {STATUSES[new_status].lower()}",
                   current["service_name"], now)
        return aid

    command_payload = [aid, action, version, payload]
    if schema["version"] == 1:
        legacy_values = forms.validate(db, user, schema, inputs, required=action == "send") if is_edit else None
        command_payload = [aid, action, legacy_values]
    return command(db, user, "application.student", key, command_payload, apply)


def student_comment(db, user, aid, note, key):
    role(user, "student")
    note = field(note, "Сообщение", 500)

    def apply():
        row = applications(db, user, aid)
        if row["status"] not in ACTIVE:
            raise DomainError(409, "Переписка доступна во время рассмотрения заявки.")
        now = timestamp()
        history_entry(db, user, aid, row["status"], now, kind="comment", note=note)
        db.execute("UPDATE application SET updated_at=? WHERE id=?", (now, aid))
        return aid

    return command(db, user, "application.student-comment", key, [aid, note], apply)


def staff_actions(row):
    if row["status"] in {"submitted", "resubmitted"}:
        return (("review", "Взять в рассмотрение"),)
    if row["status"] == "review":
        if forms.definition(row)["document_kind"]:
            positive = ("ready", "Подготовить бумажный документ" if answers(row).get("delivery") == "paper" else "Выдать PDF")
        elif forms.definition(row)["result"]:
            positive = ("completed", "Одобрить заявку")
        else:
            positive = ("completed", "Завершить заявку")
        return (positive, ("needs_changes", "Вернуть на исправление"), ("refused", "Отказать"))
    return ()


def staff_candidates(db, row):
    return list(db.execute("""
        SELECT u.id, u.full_name
        FROM staff_group g JOIN account u ON u.id = g.staff_id AND u.university_id = g.university_id
        WHERE g.university_id = ? AND g.group_id = ? AND u.role = 'staff'
        ORDER BY u.full_name
    """, (row["university_id"], row["group_id"])))


def staff_update(db, user, aid, status, note, assignee, version, key):
    role(user, "staff")
    item = applications(db, user, aid)
    requires_reason = status in {"needs_changes", "refused", "comment"} or (
        status == "completed" and bool(forms.definition(item)["result"]))
    note = field(note, "Комментарий", 500, 1 if requires_reason else 0)

    def apply():
        row = applications(db, user, aid)
        version_check(row, version)
        old = row["status"]
        assigned = row["assignee_id"]
        resolution = row["resolution"]
        now = timestamp()
        if status == "assign" and old in ACTIVE:
            if assignee not in {candidate["id"] for candidate in staff_candidates(db, row)}:
                raise DomainError(422, "Ответственный должен работать с группой заявителя.")
            if assignee == assigned:
                return aid
            assigned, new, kind = assignee, old, "assignment"
        elif status == "comment" and old in ACTIVE:
            new, kind = old, "comment"
        elif status in dict(staff_actions(row)):
            new, kind = status, "status"
            assigned = assigned or user["id"]
            if status == "ready":
                if answers(row).get("delivery") == "paper":
                    resolution = "Бумажный документ подготовлен к выдаче в демонстрационной точке."
                else:
                    issue_document(db, user, row, now)
            elif status == "completed":
                resolution = forms.definition(row)["result"]
                from .application_effects import complete
                complete(db, user, row, now)
        else:
            raise DomainError(409, "Переход недоступен для текущего статуса заявки.")
        db.execute("""
            UPDATE application SET status = ?, assignee_id = ?, updated_at = ?, resolution=?, version = version + 1
            WHERE id = ?
        """, (new, assigned, now, resolution, aid))
        history_entry(db, user, aid, new, now, kind=kind, note=note)
        if kind != "assignment":
            title = "Комментарий к заявке" if kind == "comment" else f"Заявка: {STATUSES[new].lower()}"
            notify(db, row["university_id"], row["student_id"], "application", aid,
                   f"application:{aid}:{status}:{row['version']}", title, row["service_name"], now)
        return aid

    fingerprint = [aid, status, note] if status != "assign" else [aid, status, note, assignee]
    return command(db, user, "application.status", key, fingerprint, apply)


def issue_document(db, user, row, now):
    values = answers(row)
    local_day = local_value(now, user["timezone"]).strftime("%d.%m.%Y")
    lines = [f"Получатель: {row['student_name']}", f"Группа: {row['group_name']} · Курс: {row['course_year']}",
             f"Подразделение: {row['faculty']}", f"Программа: {row['program_name']}",
             f"Дата выдачи: {local_day} · Заявка {row['id'][:8]}", f"Цель получения: {values['purpose']}"]
    if row["document_kind"] == "enrolment":
        lines.append(f"В демонстрационной базе обучающийся зачислен на период «{row['period_name']}».")
    else:
        all_periods = values["period"] == "all"
        rows = db.execute("""
            SELECT s.name, p.label, r.score, r.outcome FROM learning_result r
            JOIN subject s ON s.university_id = r.university_id AND s.id = r.subject_id
            JOIN academic_period p ON p.university_id = r.university_id AND p.id = r.period_id
            WHERE r.university_id = ? AND r.student_id = ? AND (? OR r.period_id = ?)
            ORDER BY p.starts_on DESC, s.name
        """, (row["university_id"], row["student_id"], all_periods, row["period_id"]))
        lines.append(f"Учебный период: {PERIODS[values['period']]}")
        for result in rows:
            score = f"{result['score']} / 100" if result["score"] is not None else {
                "passed": "Зачтено", "failed": "Не зачтено", None: "Результат отсутствует",
            }[result["outcome"]]
            lines.append(f"{result['name']} · {result['label']} · {score}")
    db.execute("INSERT INTO document VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
               (identifier(), row["university_id"], row["id"], user["id"], row["document_kind"],
                row["service_name"], now, render_document(row["service_name"], lines)))


def documents(db, user, document_id=None):
    if document_id is not None:
        doc = require(db.execute("SELECT * FROM document WHERE university_id = ? AND id = ?",
                                 (user["university_id"], document_id)).fetchone())
        applications(db, user, doc["application_id"])
        return doc
    role(user, "student")
    return list(db.execute("""
        SELECT d.id, d.title, d.issued_at, a.id AS application_id, a.status, json_extract(a.answers_json, '$.purpose') AS purpose
        FROM document d JOIN application a ON a.id = d.application_id AND a.university_id = d.university_id
        WHERE a.university_id = ? AND a.student_id = ? ORDER BY d.issued_at DESC
    """, (user["university_id"], user["id"])))
