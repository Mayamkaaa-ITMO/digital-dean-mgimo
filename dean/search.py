from dataclasses import dataclass
from itertools import chain

from . import applications, personal_study, personal_transfer, public_schedule, public_views, services as svc, study_materials, team_work, teams, workspace

TYPES = {"restored_note": "Восстановленные заметки", "material": "Мои материалы", "personal_subject": "Мои предметы", "subject": "Дисциплины", "task": "Задачи", "service": "Услуги",
         "application": "Мои заявки", "document": "Мои документы", "team": "Команды", "shared_note": "Общие заметки"}
PUBLIC_TYPES = {"subject_card": "Карточки дисциплин", "restored_note": "Восстановленные заметки", "material": "Мои материалы", "personal_subject": "Мои предметы", "subject": "Занятия", "room": "Аудитории", "service": "Сервисы", "note": "Мои заметки"}


@dataclass(frozen=True)
class Match:
    kind: str
    title: str
    href: str
    summary: str = ""


def accessible(db, user):
    workspace.scope(user)
    for period in svc.periods(db, user):
        for row in svc.results(db, user, period["id"]):
            yield Match("subject", row["subject"], f"/grades/{row['id']}", period["label"])
    for row in workspace.tasks(db, user):
        yield Match("task", row["title"], f"/tasks/{row['id']}", row["description"])
    for row in applications.catalog(db, user):
        yield Match("service", row["name"], f"/services/{row['id']}", row["description"])
    for row in applications.applications(db, user):
        values = " · ".join(value for value in applications.answers(row).values() if isinstance(value, str) and value)
        yield Match("application", row["service_name"], f"/requests/{row['id']}",
                    " · ".join(filter(None, (applications.STATUSES[row["status"]], values))))
    for row in applications.documents(db, user):
        yield Match("document", row["title"], f"/documents/{row['id']}", applications.STATUSES[row["status"]])
    for team in teams.lists(db, user):
        tid = team["id"]
        yield Match("team", team["title"], f"/teams/{tid}")
        for row in team_work.tasks(db, user, tid):
            yield Match("task", row["title"], f"/teams/{tid}/tasks/{row['id']}", team["title"])
        for row in team_work.shared_notes(db, user, tid):
            yield Match("shared_note", row["body"][:100], f"/teams/{tid}/notes/{row['id']}", row["body"])


def public_matches(db, user, selection):
    for row in db.execute("""
        SELECT id,subject,kind FROM public_lesson WHERE source_id=? AND group_id=? AND cancelled=0
        ORDER BY starts_at,id
    """, selection):
        yield Match("subject", row["subject"], f"/schedule/public/{row['id']}", row["kind"])
    from .public_subjects import subject_href
    for title in personal_study.suggestions(db, selection):
        yield Match("subject_card", title, subject_href(selection, title), personal_study.period(selection))
    for path, name in public_views.rooms(db, selection).items():
        yield Match("room", name if path.startswith("ref:") else "Аудитория " + name, public_views.room_href(path))
    source = public_schedule.SOURCES[selection[0]]
    group = source["groups"][selection[1]]
    for row in group.get("pattern", ()):
        yield Match("subject", row["subject"], "/schedule",
                    f'Справочная сетка · {row["day"]} · {row["start"]}')
    for service in source["services"]:
        yield Match("service", service["label"], "/student-services", service.get("address", "Официальный источник"))
    for row in db.execute("""
        SELECT p.subject,p.id,n.body FROM public_note n JOIN public_lesson p ON p.id=n.lesson_id
        WHERE n.owner_id=? AND p.source_id=? AND p.group_id=? AND n.body<>''
        ORDER BY n.updated_at DESC
    """, (user["id"], *selection)):
        yield Match("note", row["subject"], f"/schedule/public/{row['id']}", row["body"])


def personal_matches(db, user):
    for row in personal_study.subjects(db, user):
        yield Match("personal_subject", row["title"], f"/study/subjects/{row['id']}", row["note"])
    for row in personal_transfer.detached(db, user):
        yield Match("restored_note", row["title"], "/profile/data", row["body"])
    for row in study_materials.search_rows(db, user):
        excerpt = row["text_excerpt"].decode("utf-8", errors="ignore") if row["text_excerpt"] else ""
        yield Match("material", row["title"], f"/study/subjects/{row['subject_id']}/materials",
                    " · ".join(filter(None, (row["subject"], row["value"], excerpt))))


def matches(db, user, raw, kind="", selection=None):
    words = raw[:120].casefold().split()
    if not words:
        return []
    records = public_matches(db, user, selection) if selection else accessible(db, user)
    return [item for item in chain(records, personal_matches(db, user)) if (not kind or item.kind == kind)
            and all(word in (item.title + " " + item.summary).casefold() for word in words)]
