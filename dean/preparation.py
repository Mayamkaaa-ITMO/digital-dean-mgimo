"""Deterministic study blocks over confirmed calendar intervals."""
from collections import defaultdict
from datetime import datetime, timedelta
import json

from . import calendar_tools as cal, public_schedule, services, study_context, workspace
from .clock import now
from .domain import DomainError, identifier, require, role, timestamp
from .learning import optional_int
from .schedule import edit_time, local_value

MAX_TASKS = 25
MAX_BLOCKS = 100


def effort(db, tid):
    row = db.execute("SELECT minutes FROM task_effort WHERE task_id=?", (tid,)).fetchone()
    return row["minutes"] if row else None


def set_effort(db, user, tid, raw, key):
    minutes = optional_int(raw, "Минут на задачу", 1440)
    if minutes == 0:
        raise DomainError(422, "Укажи длительность от одной минуты или оставь поле пустым.")

    def apply():
        workspace.task(db, user, tid)
        if minutes is None:
            db.execute("DELETE FROM task_effort WHERE task_id=?", (tid,))
        else:
            db.execute("INSERT INTO task_effort VALUES (?,?) ON CONFLICT(task_id) DO UPDATE SET minutes=excluded.minutes",
                       (tid, minutes))
        return tid
    return services.command(db, user, "task.effort", key, [tid, minutes], apply)


def exam_deadline(db, user, selection, reference):
    kind, _, event_id = reference.partition(":")
    if kind == "demo" and not selection:
        row = services.results(db, user, result_id=event_id)
        return row["exam_at"]
    if kind == "public" and selection:
        row = public_schedule.local_lesson(db, selection, event_id)
        if row["kind"] == "Экзамен" and not row["cancelled"]:
            return row["starts_at"]
    raise DomainError(422, "Выбери экзамен из текущего контекста.")


def exams(db, user, selection):
    if not selection:
        return [(f'demo:{row["id"]}', row["subject"], row["exam_at"]) for row in services.results(db, user)
                if row["exam_at"] and row["exam_at"] >= timestamp()]
    return [(f'public:{row["id"]}', row["subject"], row["starts_at"]) for row in db.execute("""
        SELECT id,subject,starts_at FROM public_lesson
        WHERE source_id=? AND group_id=? AND kind='Экзамен' AND cancelled=0 AND starts_at>=? ORDER BY starts_at
    """, (*selection, timestamp()))]


def state(db, user, pid=None):
    role(user, "student")
    if pid:
        return require(db.execute("SELECT * FROM study_preparation WHERE id=? AND owner_id=?", (pid,user["id"])).fetchone())
    return list(db.execute("""
        SELECT id,deadline_at,state FROM study_preparation WHERE owner_id=? ORDER BY created_at DESC,id LIMIT 30
    """, (user["id"],)))


def integer(raw, label, minimum, maximum):
    value = optional_int(raw, label, maximum)
    if value is None or value < minimum:
        raise DomainError(422, f"Укажи значение «{label}» от {minimum} до {maximum}.")
    return value


def rule_set(db, user, selection, fields):
    zone = cal.period_for(user, selection).timezone
    exam = fields.get("exam", "")
    explicit = fields.get("deadline")
    deadline = exam_deadline(db, user, selection, exam) if exam else timestamp(edit_time(explicit, zone)) if explicit else None
    if not deadline:
        raise DomainError(422, "Срок подготовки неизвестен. Выбери экзамен или введи срок.")
    first = study_context.date_value(fields.get("first"))
    last = min(first + timedelta(days=30), local_value(deadline, zone).date())
    first, last, low, high = cal.window(first.isoformat(), last.isoformat(), fields.get("from"), fields.get("to"), "all")
    if deadline <= timestamp() or last < now().astimezone(local_value(deadline, zone).tzinfo).date():
        raise DomainError(422, "До выбранного срока нет будущих дней подготовки.")
    daily = integer(fields.get("daily"), "Минут в день", 1, 720)
    block = integer(fields.get("block"), "Минут за подход", 1, 120)
    rest = integer(fields.get("rest"), "Минут перерыва", 0, 90)
    return deadline, exam, (first, last, low, high, daily, block, rest)


def allocated(db, user):
    counts = defaultdict(int)
    for row in db.execute("""
        SELECT l.task_id,e.starts_at,e.ends_at FROM study_preparation_event l JOIN personal_event e ON e.id=l.event_id
        WHERE e.owner_id=? AND e.ends_at>? AND l.task_id IS NOT NULL AND e.all_day=0
    """, (user["id"], timestamp())):
        counts[row["task_id"]] += duration(row["starts_at"], row["ends_at"])
    return counts


def duration(start, end):
    return max(0, int((datetime.fromisoformat(end)-datetime.fromisoformat(start)).total_seconds()//60))


def propose(db, user, selection, deadline, rules, target):
    first, last, low, high, daily, block, rest = rules
    zone = cal.period_for(user, selection).timezone
    tasks = workspace.tasks(db, user, state="todo")
    if target:
        chosen = workspace.task(db, user, target)
        if chosen["state"] != "todo":
            raise DomainError(409, "Задача уже завершена.")
        tasks = [chosen]
    tasks.sort(key=lambda task: (task["due_at"] or deadline, task["priority"]!="high", task["title"].casefold(), task["id"]))
    known = allocated(db, user)
    todo, remaining = [], []
    for task in tasks[:MAX_TASKS]:
        minutes = effort(db, task["id"])
        if minutes is None:
            remaining.append({"title": task["title"], "reason": "Оцени длительность"})
        elif minutes > known[task["id"]]:
            deadline_task = min(task["due_at"] or deadline, deadline)
            todo.append({"id": task["id"], "title": task["title"], "left": minutes-known[task["id"]], "deadline": deadline_task})
    if len(tasks) > MAX_TASKS:
        remaining.append({"title": "Остальные задачи", "reason": f"Черновик охватывает первые {MAX_TASKS} задач по сроку"})
    events = cal.calendar(db, user, selection, first, last+timedelta(days=1))
    used = defaultdict(int)
    next_start = timestamp((now()+timedelta(seconds=59)).replace(second=0, microsecond=0))
    proposals = []
    for day, start, end in cal.free_windows(events, first, last, low, high, zone, "all"):
        cursor = max(start, next_start)
        end = min(end, deadline)
        while cursor < end and used[day] < daily and len(proposals) < MAX_BLOCKS:
            task = next((entry for entry in todo if entry["left"] and duration(cursor, min(end, entry["deadline"]))), None)
            if task is None:
                break
            minutes = min(task["left"], daily-used[day], block, duration(cursor, min(end, task["deadline"])))
            finish = timestamp(datetime.fromisoformat(cursor)+timedelta(minutes=minutes))
            proposals.append({"task": task["id"], "title": task["title"], "start": cursor, "end": finish})
            task["left"] -= minutes
            used[day] += minutes
            cursor = next_start = timestamp(datetime.fromisoformat(finish)+timedelta(minutes=rest))
    remaining.extend({"title": task["title"], "reason": f'Осталось {task["left"]} мин'} for task in todo if task["left"])
    return proposals, remaining


def create(db, user, selection, fields, key):
    role(user, "student")

    def apply():
        cal.verify_context(db, user, selection, fields.get("context_revision"))
        deadline, exam, rules = rule_set(db, user, selection, fields)
        blocks, remaining = propose(db, user, selection, deadline, rules, fields.get("task"))
        data = [item.isoformat() if hasattr(item, "isoformat") else item for item in rules]
        pid = identifier()
        db.execute("""
            INSERT INTO study_preparation(id,owner_id,source_id,group_id,deadline_at,exam,rules_json,
                                          blocks_json,remaining_json,created_at) VALUES (?,?,?,?,?,?,?,?,?,?)
        """, (pid, user["id"], *(selection or study_context.DEMO), deadline, exam, json.dumps(data),
              json.dumps(blocks, ensure_ascii=False), json.dumps(remaining, ensure_ascii=False), timestamp()))
        return pid
    return services.command(db, user, "study.prepare", key, [selection, fields], apply)


def review_reason(db, user, row, *, existing=False):
    selection = (row["source_id"], row["group_id"]) if row["source_id"] else None
    if row["exam"]:
        try:
            value = exam_deadline(db, user, selection, row["exam"])
        except DomainError:
            value = None
        if value != row["deadline_at"]:
            return "Экзамен перенесён или отменён. Нужен новый расчёт."
    if existing:
        blocks = db.execute("""
            SELECT l.event_id,e.starts_at,e.ends_at FROM study_preparation_event l
            JOIN personal_event e ON e.id=l.event_id WHERE l.plan_id=? AND e.ends_at>?
        """, (row["id"], timestamp())).fetchall()
        for block in blocks:
            if cal.busy(db, user, selection, block["starts_at"], block["ends_at"], visibility="study"):
                return "Календарь изменился. Найди новый интервал и перенеси своё событие."
    return ""


def verify(db, user, selection, plan, blocks):
    first, last, low, high, daily, _, rest = json.loads(plan["rules_json"])
    zone = cal.period_for(user, selection).timezone
    used, requested, last_end = defaultdict(int), defaultdict(int), None
    booked = allocated(db, user)
    for block in sorted(blocks, key=lambda value: value["start"]):
        start, end = block["start"], block["end"]
        day = local_value(start, zone).date().isoformat()
        task = workspace.task(db, user, block["task"])
        bound = min(task["due_at"] or plan["deadline_at"], plan["deadline_at"])
        if start < timestamp() or not start < end <= bound or task["state"] != "todo":
            raise DomainError(409, "Задача завершена или срок изменился. Пересчитай план.")
        lower, upper = cal.day_boundary(study_context.date_value(day), cal.minute_value(low[:5]), zone), cal.day_boundary(
            study_context.date_value(day), cal.minute_value(high[:5]), zone)
        if not first <= day <= last or not lower <= start < end <= upper:
            raise DomainError(422, "Перенеси блок в заданные часы подготовки.")
        if last_end and datetime.fromisoformat(start)-datetime.fromisoformat(last_end) < timedelta(minutes=rest):
            raise DomainError(422, "Сохрани перерыв между блоками.")
        last_end = end
        minutes = duration(start, end)
        used[day] += minutes
        requested[task["id"]] += minutes
        if used[day] > daily or requested[task["id"]] + booked[task["id"]] > (effort(db, task["id"]) or 0):
            raise DomainError(409, "Нагрузка или длительность задачи изменилась.")
        if cal.busy(db, user, selection, start, end):
            raise DomainError(409, "В календаре появился занятый интервал. Пересчитай или перенеси блок.")


def change(db, user, selection, pid, action, fields, key):
    def apply():
        plan = state(db, user, pid)
        if action == "confirm" and plan["state"] == "confirmed":
            return pid
        if plan["state"] != "draft" or str(plan["version"]) != fields.get("version"):
            raise DomainError(409, "Черновик изменился. Открой его заново.")
        if action == "cancel":
            db.execute("UPDATE study_preparation SET state='cancelled',version=version+1 WHERE id=?", (pid,))
            return pid
        cal.verify_context(db, user, selection, fields.get("context_revision"))
        if (plan["source_id"], plan["group_id"]) != (selection or study_context.DEMO):
            raise DomainError(409, "Вернись в контекст исходного расчёта.")
        reason = review_reason(db, user, plan)
        if reason:
            raise DomainError(409, reason)
        blocks = json.loads(plan["blocks_json"])
        if not blocks:
            raise DomainError(422, "Блоков для подтверждения нет.")
        if action == "move":
            number = integer(fields.get("index"), "Номер блока", 0, len(blocks)-1)
            block = blocks[number]
            zone = cal.period_for(user, selection).timezone
            new_start = edit_time(fields.get("starts"), zone)
            block["end"] = timestamp(new_start+timedelta(minutes=duration(block["start"],block["end"])))
            block["start"] = timestamp(new_start)
            verify(db, user, selection, plan, [block])
            # Other draft blocks remain editable even when an independent conflict appears.
            rest = json.loads(plan["rules_json"])[-1]
            gap = timedelta(minutes=rest)
            start, end = datetime.fromisoformat(block["start"]), datetime.fromisoformat(block["end"])
            if any(other is not block and start-gap < datetime.fromisoformat(other["end"])
                   and end+gap > datetime.fromisoformat(other["start"]) for other in blocks):
                raise DomainError(422, "Сохрани перерыв между блоками.")
            db.execute("UPDATE study_preparation SET blocks_json=?,version=version+1 WHERE id=?",
                       (json.dumps(blocks, ensure_ascii=False), pid))
        elif action == "confirm":
            verify(db, user, selection, plan, blocks)
            for block in blocks:
                event = workspace.add_event(db, user, ("Подготовка · "+block["title"][:106], "", "",
                                                       block["start"], block["end"], 0, None))
                db.execute("INSERT INTO study_preparation_event VALUES (?,?,?)", (pid, event, block["task"]))
            db.execute("UPDATE study_preparation SET state='confirmed',version=version+1 WHERE id=?", (pid,))
        return pid
    return services.command(db, user, "study.prepare."+action, key, [pid, fields, selection], apply)
