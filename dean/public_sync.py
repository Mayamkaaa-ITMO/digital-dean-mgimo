"""Normalize, review and publish one source week without replacing user data."""
from datetime import timedelta
import json

from . import public_schedule as catalog
from . import public_transport as transport, public_tusur
from .clock import now
from .db import write
from .domain import DomainError, identifier, require, role, timestamp
from .services import notify


def week_row(db, week):
    return db.execute("""
        SELECT * FROM public_week WHERE source_id=? AND group_id=? AND monday=?
    """, week.key).fetchone()


def stage_in_transaction(db, week, events, provenance, observed_at):
    if not events:
        raise DomainError(422, "Пустой сетки недостаточно для публикации недели.")
    db.execute("INSERT OR IGNORE INTO public_week (source_id,group_id,monday) VALUES (?,?,?)", week.key)
    row = week_row(db, week)
    preview = identifier()
    db.execute("""
        INSERT INTO public_preview VALUES (?,?,?,?,?,?,?,?,?,NULL)
    """, (preview, *week.key, row["revision"], catalog.event_json(events),
          provenance, observed_at, timestamp()))
    return preview


def stage_sample(db, week):
    if week.monday.isoformat() not in week.group["weeks"]:
        raise DomainError(422, "Для этой недели нет сохранённого снимка.")
    events = catalog.sample_events(week)
    with write(db):
        return stage_in_transaction(db, week, events, "snapshot", catalog.SOURCES[week.source_id]["observed_at"])


def read_preview(db, key):
    return require(db.execute("SELECT * FROM public_preview WHERE id=?", (key,)).fetchone())


def pair_values(row):
    return (row["subject"], row["kind"], row["starts_at"], row["ends_at"],
            row["rooms_json"], row["cancelled"])


def proposed_values(event):
    return (event["subject"], event["kind"], event["starts_at"], event["ends_at"],
            catalog.dump(event["rooms"]), int(event["cancelled"]))


def rollback_info(db, preview_id):
    return db.execute("SELECT * FROM public_rollback WHERE preview_id=?", (preview_id,)).fetchone()


def stage_rollback(db, user, restore_id):
    require_operator(db, user)
    with write(db):
        previous = read_preview(db, restore_id)
        if not previous["applied_at"]:
            raise DomainError(409, "Для отката нужна ранее опубликованная версия.")
        week = catalog.week_scope(previous["source_id"], previous["group_id"], previous["monday"])
        events = [catalog.Event(**item) for item in json.loads(previous["events_json"])]
        existing = {item.external_id for item in events}
        for row in db.execute("""
            SELECT * FROM public_lesson WHERE source_id=? AND group_id=? AND monday=? AND cancelled=0
        """, week.key):
            if row["external_id"] not in existing:
                events.append(catalog.Event(external_id=row["external_id"], subject=row["subject"], kind=row["kind"],
                                            starts_at=row["starts_at"], ends_at=row["ends_at"],
                                            rooms=json.loads(row["rooms_json"]), cancelled=True))
        key = stage_in_transaction(db, week, events, previous["provenance"], previous["observed_at"])
        db.execute("INSERT INTO public_rollback VALUES (?,?,?,?,NULL,NULL)", (key, restore_id, user["id"], timestamp()))
        return key


def differences(db, preview):
    week = catalog.week_scope(preview["source_id"], preview["group_id"], preview["monday"])
    current = {row["external_id"]: row for row in db.execute("""
        SELECT * FROM public_lesson WHERE source_id=? AND group_id=? AND monday=?
    """, week.key)}
    restoring = rollback_info(db, preview["id"]) is not None
    seen = set()
    added, changed, cancelled, ambiguous = [], [], [], []
    for event in json.loads(preview["events_json"]):
        key = event["external_id"]
        if key in seen:
            raise DomainError(422, "Во входных данных есть неоднозначный идентификатор.")
        seen.add(key)
        previous = current.get(key)
        if previous is None:
            if event["cancelled"]:
                ambiguous.append((event, previous))
            else:
                added.append((event, previous))
        elif previous["subject"] != event["subject"] and not restoring:
            ambiguous.append((event, previous))
        elif pair_values(previous) != proposed_values(event):
            (cancelled if event["cancelled"] else changed).append((event, previous))
    missing = [row for key, row in current.items() if key not in seen and not row["cancelled"]]
    return {"added": added, "changed": changed, "cancelled": cancelled, "ambiguous": ambiguous, "missing": missing}


def describe(db, key):
    preview = read_preview(db, key)
    return {name: len(records) for name, records in differences(db, preview).items()}


def notify_change(db, event_id, version, event, stamp):
    db.execute("""
        UPDATE notification SET active=0
        WHERE kind='public_reminder' AND target_id=?
    """, (event_id,))
    title = "Пара отменена" if event["cancelled"] else "Публичная пара обновлена"
    for user in db.execute("""
        SELECT a.id,a.university_id FROM account a JOIN public_lesson e ON e.id=?
        WHERE a.role='student' AND (
            EXISTS (SELECT 1 FROM public_note n WHERE n.lesson_id=e.id AND n.owner_id=a.id AND n.remind=1)
            OR EXISTS (SELECT 1 FROM public_group_follow f
                       WHERE f.owner_id=a.id AND f.source_id=e.source_id AND f.group_id=e.group_id)
        )
    """, (event_id,)):
        notify(db, user["university_id"], user["id"], "public_change", event_id,
               f"public-change:{event_id}:{version}", title, event["subject"], stamp)


def publish(db, key, *, operator=None):
    with write(db):
        preview = read_preview(db, key)
        week = catalog.week_scope(preview["source_id"], preview["group_id"], preview["monday"])
        previous = week_row(db, week)
        restoring = rollback_info(db, key)
        if restoring and operator is not None:
            require_operator(db, operator)
        if preview["applied_at"]:
            return {name: 0 for name in differences(db, preview)}
        if previous["revision"] != preview["base_revision"]:
            raise DomainError(409, "Неделя изменилась после просмотра. Создай новое сравнение.")
        if not restoring and previous["provenance"] == "live" and preview["provenance"] != "live":
            raise DomainError(409, "Сохранённый снимок старше опубликованного живого ответа.")
        if (not restoring and preview["provenance"] == "live" and previous["live_observed_at"]
                and previous["live_preview_id"] != key):
            raise DomainError(409, "Источник уже прислал более свежую версию. Открой последнее сравнение.")
        diff = differences(db, preview)
        if diff["ambiguous"]:
            raise DomainError(409, "Сверь неоднозначные идентификаторы с первоисточником перед публикацией.")
        if diff["missing"]:
            raise DomainError(409, "В ответе отсутствуют занятия. Предыдущая версия сохранена.")
        stamp = timestamp()
        for event, old in diff["added"] + diff["changed"] + diff["cancelled"]:
            event_id = catalog.lesson_id(week, event["external_id"])
            values = proposed_values(event)
            if old is None:
                db.execute("""
                    INSERT INTO public_lesson
                    (id,source_id,group_id,monday,external_id,subject,kind,starts_at,ends_at,rooms_json,cancelled)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?)
                """, (event_id, *week.key, event["external_id"], *values))
            else:
                db.execute("""
                    UPDATE public_lesson
                    SET subject=?,kind=?,starts_at=?,ends_at=?,rooms_json=?,cancelled=?,version=version+1 WHERE id=?
                """, (*values, event_id))
                notify_change(db, event_id, old["version"] + 1, event, stamp)
            if old is not None or previous["revision"] > 0:
                db.execute("INSERT INTO public_lesson_change VALUES (?,?,?,?,?,?)",
                           (event_id, old["version"] + 1 if old else 1,
                            catalog.dump(pair_values(old)) if old else None, catalog.dump(values),
                            "rollback" if restoring else preview["provenance"], stamp))
                if old is None:
                    notify_change(db, event_id, 1, event, stamp)
        if (restoring or diff["added"] or diff["changed"] or diff["cancelled"]
                or previous["provenance"] != preview["provenance"]):
            db.execute("""
                UPDATE public_week SET revision=revision+1, provenance=?, observed_at=?, published_at=?
                WHERE source_id=? AND group_id=? AND monday=?
            """, (preview["provenance"], preview["observed_at"], stamp, *week.key))
        if restoring:
            db.execute("UPDATE public_rollback SET applied_at=?,approved_by=? WHERE preview_id=?",
                       (stamp, operator["id"] if operator is not None else None, key))
        db.execute("UPDATE public_preview SET applied_at=? WHERE id=?", (stamp, key))
        return {name: len(records) for name, records in diff.items()}


def allow_operator(db, username):
    with write(db):
        user = require(db.execute("SELECT * FROM account WHERE username=?", (username,)).fetchone())
        role(user, "staff")
        db.execute("INSERT OR IGNORE INTO public_operator VALUES (?)", (user["id"],))


def operator(db, user):
    return user["role"] == "staff" and db.execute(
        "SELECT 1 FROM public_operator WHERE account_id=?", (user["id"],)
    ).fetchone() is not None


def require_operator(db, user):
    if not operator(db, user):
        raise DomainError(403, "Эта операция доступна оператору открытого источника.")


def reserve_request(db, week):
    moment = now()
    stamp = timestamp(moment)
    expired = timestamp(moment - timedelta(minutes=3))
    with write(db):
        db.execute("""
            UPDATE public_attempt SET result='worker_expired',finished_at=? WHERE source_id=? AND result='running'
            AND started_at<?
        """, (stamp, week.source_id, expired))
        rows = list(db.execute("""
            SELECT * FROM public_attempt WHERE source_id=? AND result<>'disabled'
            ORDER BY rowid DESC LIMIT 1
        """, (week.source_id,)))
        if rows:
            last = rows[0]
            if last["result"] in {"access_blocked", "challenge"}:
                raise DomainError(409, "Канал остановлен после ограничения доступа. Требуется проверка источника.")
            if last["result"] == "running":
                raise DomainError(409, "Запрос к этому источнику уже выполняется.")
            if ((last["retry_at"] and last["retry_at"] > stamp)
                    or last["started_at"] > timestamp(moment - timedelta(minutes=1))):
                raise DomainError(429, "Для этого источника ещё действует пауза между запросами.")
        recent = db.execute("""
            SELECT 1 FROM public_attempt WHERE source_id=? AND group_id=? AND monday=?
            AND result IN ('parsed','not_modified') AND finished_at>?
            LIMIT 1
        """, (*week.key, timestamp(moment - timedelta(hours=6)))).fetchone()
        if recent:
            raise DomainError(429, "Для этой недели есть ответ за последние шесть часов.")
        attempt = identifier()
        db.execute("INSERT INTO public_attempt VALUES (?,?,?,?,?,NULL,'running',NULL)",
                   (attempt, *week.key, stamp))
        row = week_row(db, week)
        return attempt, row["etag"] if row else None


def finish_request(db, attempt, result, retry=None):
    db.execute("""
        UPDATE public_attempt SET finished_at=?, result=?, retry_at=? WHERE id=? AND result='running'
    """, (timestamp(), result, timestamp(retry) if retry else None, attempt))


def stage_live(db, week, read=transport.read_week):
    if not catalog.live_available(week.source_id):
        with write(db):
            stamp = timestamp()
            db.execute("INSERT INTO public_attempt VALUES (?,?,?,?,?,?,'disabled',NULL)",
                       (identifier(), *week.key, stamp, stamp))
        raise DomainError(409, "Живой канал отключён. Для этой группы доступен официальный сохранённый снимок.")
    attempt, etag = reserve_request(db, week)
    try:
        content, tag = read(week, etag)
        events = public_tusur.parse(week, content) if content is not None else None
        if content is not None and not events:
            raise ValueError("empty_week")
        if content is None and not etag:
            raise ValueError("unexpected_304")
    except transport.FetchError as exc:
        pause = exc.retry_after
        if not pause and exc.code not in {"access_blocked", "challenge"}:
            pause = now() + timedelta(minutes=15)
        with write(db):
            finish_request(db, attempt, exc.code, pause)
        raise DomainError(503, f"Источник не ответил пригодными данными ({exc.code}).") from exc
    except ValueError as exc:
        reason = "challenge" if str(exc) == "challenge" else "parse_failed"
        with write(db):
            finish_request(db, attempt, reason, now() + timedelta(minutes=15))
        raise DomainError(422, "Ответ не прошёл проверку структуры, недели или полей.") from exc
    with write(db):
        state = db.execute("SELECT result FROM public_attempt WHERE id=?", (attempt,)).fetchone()
        if state["result"] != "running":
            raise DomainError(409, "Результат устарел. Выполни новый запрос.")
        if events is None:
            finish_request(db, attempt, "not_modified")
            return None
        previous = week_row(db, week)
        pending = pending_live(db, week)
        if pending and pending["events_json"] == catalog.event_json(events):
            preview = pending["id"]
        elif previous and previous["provenance"] == "live" and unchanged(db, week, events):
            preview = None
        else:
            preview = stage_in_transaction(db, week, events, "live", timestamp())
        db.execute("""
            UPDATE public_week SET etag=?,live_preview_id=?,live_observed_at=?
            WHERE source_id=? AND group_id=? AND monday=?
        """, (tag, preview, timestamp(), *week.key))
        finish_request(db, attempt, "parsed")
        return preview


def pending_live(db, week):
    return db.execute("""
        SELECT p.* FROM public_preview p JOIN public_week w USING(source_id,group_id,monday)
        WHERE p.source_id=? AND p.group_id=? AND p.monday=? AND p.provenance='live'
        AND p.id=w.live_preview_id AND p.base_revision=w.revision AND p.applied_at IS NULL
    """, week.key).fetchone()


def unchanged(db, week, events):
    existing = {row["external_id"]: pair_values(row) for row in db.execute("""
        SELECT * FROM public_lesson WHERE source_id=? AND group_id=? AND monday=? AND cancelled=0
    """, week.key)}
    proposed = {event.external_id: (
        event.subject, event.kind, event.starts_at, event.ends_at, catalog.dump(event.rooms), int(event.cancelled)
    ) for event in events}
    return existing == proposed
