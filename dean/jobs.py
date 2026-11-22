"""Durable, leased reminder refresh over local records only."""

from datetime import timedelta
import sqlite3

from . import public_refresh, public_schedule, reminders
from .clock import now
from .db import schema_version, write
from .domain import DomainError, identifier, timestamp

MAX_FAILURES = 5
LEASE = timedelta(minutes=5)
INTERVAL = timedelta(minutes=2)


def enqueue(db, current):
    stamp = timestamp(current)
    with write(db):
        previous = db.execute("SELECT observed_at FROM reminder_worker_clock WHERE slot=1").fetchone()
        if previous and previous["observed_at"] > stamp:
            raise DomainError(409, "Часы опережали текущее время. Проверь системное время перед запуском заданий.")
        db.execute("""
            INSERT INTO reminder_worker_clock VALUES (1,?)
            ON CONFLICT(slot) DO UPDATE SET observed_at=excluded.observed_at
        """, (stamp,))
        db.execute("""
            UPDATE reminder_job SET attempts=attempts+1,lease_id=NULL,lease_until=NULL,error_code='expired_lease'
            WHERE lease_until<=?
        """, (stamp,))
        db.execute("""
            INSERT OR IGNORE INTO reminder_job(owner_id,available_at)
            SELECT id,? FROM account WHERE role='student'
        """, (stamp,))


def claim(db, current):
    stamp = timestamp(current)
    with write(db):
        row = db.execute("""
            SELECT j.owner_id FROM reminder_job j JOIN account a ON a.id=j.owner_id AND a.role='student'
            WHERE j.available_at<=? AND j.attempts<? AND (j.lease_until IS NULL OR j.lease_until<=?)
            ORDER BY j.available_at,j.owner_id LIMIT 1
        """, (stamp, MAX_FAILURES, stamp)).fetchone()
        if row is None:
            return None
        lease = identifier()
        db.execute("""
            UPDATE reminder_job SET lease_id=?,lease_until=?,last_started_at=? WHERE owner_id=?
        """, (lease, timestamp(current+LEASE), stamp, row["owner_id"]))
        return row["owner_id"], lease


def perform(db, job, current, refresh):
    with write(db):
        row = db.execute("""
            SELECT a.*,u.timezone FROM reminder_job j JOIN account a ON a.id=j.owner_id
            JOIN university u ON u.id=a.university_id
            WHERE a.id=? AND j.lease_id=? AND a.role='student'
        """, job).fetchone()
        if not row:
            return False
        refresh(db, row, current)
        db.execute("""
            UPDATE reminder_job SET lease_id=NULL,lease_until=NULL,attempts=0,available_at=?,
                last_success_at=?,error_code=NULL WHERE owner_id=? AND lease_id=?
        """, (timestamp(current+INTERVAL), timestamp(current), *job))
        return True


def fail(db, job, current, kind):
    with write(db):
        row = db.execute("SELECT attempts FROM reminder_job WHERE owner_id=? AND lease_id=?", job).fetchone()
        if row:
            failures = row["attempts"]+1
            delay = timedelta(minutes=min(30, 2**(failures-1)))
            db.execute("""
                UPDATE reminder_job SET attempts=?,available_at=?,lease_id=NULL,lease_until=NULL,error_code=?
                WHERE owner_id=? AND lease_id=?
            """, (failures, timestamp(current+delay), kind, *job))


def run_once(db, limit=100, *, refresh=reminders.refresh_in_transaction):
    if not 1<=limit<=500:
        raise DomainError(422, "Выбери до 500 заданий.")
    current = now()
    enqueue(db, current)
    result = {"completed":0,"failed":0,"superseded":0}
    for _ in range(limit):
        job = claim(db, current)
        if not job:
            break
        try:
            active = perform(db, job, current, refresh)
        except Exception as error:
            kind = "storage" if isinstance(error, sqlite3.Error) else "refresh"
            fail(db, job, current, kind)
            result["failed"] += 1
        else:
            result["completed" if active else "superseded"] += 1
    return result


def retry(db):
    current = now()
    enqueue(db, current)
    with write(db):
        return db.execute("""
            UPDATE reminder_job SET attempts=0,available_at=? WHERE attempts>=? AND lease_id IS NULL
        """, (timestamp(current),MAX_FAILURES)).rowcount


def source_state(db, source_id):
    source = public_schedule.SOURCES[source_id]
    mode = "saved_snapshot" if source.get("sample_file") or source_id == public_schedule.TUSUR["source_id"] else "reference_grid" if any(
        group.get("pattern") for group in source["groups"].values()) else "link"
    published = db.execute("""
        SELECT count(*) AS weeks,min(monday) AS from_week,max(monday) AS through_week,
               max(observed_at) AS content_read_at,max(published_at) AS last_publication
        FROM public_week WHERE source_id=? AND revision>0
    """, (source_id,)).fetchone()
    latest = db.execute("""
        SELECT result,finished_at FROM public_attempt WHERE source_id=? AND finished_at IS NOT NULL
        ORDER BY rowid DESC LIMIT 1
    """, (source_id,)).fetchone()
    fetch = db.execute("""
        SELECT max(finished_at) FROM public_attempt WHERE source_id=? AND result IN ('parsed','not_modified')
    """, (source_id,)).fetchone()[0]
    return {"source":source_id,"configured_mode":mode,"adapter_local":source_id=="tusur-timetable",
            "live_enabled":public_schedule.live_available(source_id),"scheduled":public_refresh.scheduled(source_id),
            **dict(published),"last_successful_fetch":fetch,"last_attempt":dict(latest) if latest else None}


def diagnostics(db):
    stamp = timestamp()
    rows = db.execute("""
        SELECT count(*) AS registered,
               coalesce(sum(lease_until>?),0) AS leased,
               coalesce(sum(attempts>=?),0) AS exhausted,
               max(last_success_at) AS last_success
        FROM reminder_job
    """, (stamp,MAX_FAILURES)).fetchone()
    error = db.execute("""
        SELECT error_code FROM reminder_job WHERE error_code IS NOT NULL ORDER BY last_started_at DESC LIMIT 1
    """).fetchone()
    return {"application":"university-five-night","schema":db.execute("PRAGMA user_version").fetchone()[0],
            "expected_schema":schema_version(),"integrity_ok":db.execute("PRAGMA quick_check").fetchone()[0]=="ok",
            "foreign_keys_ok":db.execute("PRAGMA foreign_key_check").fetchone() is None,
            "sources":[source_state(db,source) for source in public_schedule.SOURCES],
            "reminders":{**dict(rows),"last_error":error[0] if error else None}}
