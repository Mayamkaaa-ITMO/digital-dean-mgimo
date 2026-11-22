"""One bounded background refresh of the documented TUSUR groups."""

from datetime import timedelta
import os
from zoneinfo import ZoneInfo

from . import public_schedule as catalog, public_sync, public_transport
from .clock import now
from .domain import DomainError, timestamp


def groups():
    return tuple(dict.fromkeys(group.strip() for group in os.getenv("DEAN_TUSUR_AUTO_GROUPS", "").split(",")
                               if group.strip()))


def scheduled(source_id):
    return source_id == catalog.TUSUR["source_id"] and catalog.live_available(source_id) and bool(groups())


def recent(db, week, since):
    return db.execute("""
        SELECT max(finished_at) FROM public_attempt
        WHERE source_id=? AND group_id=? AND monday=? AND result IN ('parsed','not_modified') AND finished_at>=?
    """, (*week.key, since)).fetchone()[0]


def eligible(db, source_id, configured, moment):
    local_day = moment.astimezone(ZoneInfo(catalog.TUSUR["zone"])).date()
    monday = local_day - timedelta(days=local_day.weekday())
    candidates = []
    for group in configured:
        for date in (monday, monday + timedelta(days=7)):
            iso = date.isoformat()
            if iso in catalog.known_weeks(source_id, group):
                week = catalog.week_scope(source_id, group, iso)
                last = recent(db, week, timestamp(moment - timedelta(hours=6)))
                if not last:
                    last_attempt = db.execute("""
                        SELECT max(started_at) FROM public_attempt WHERE source_id=? AND group_id=? AND monday=?
                    """, week.key).fetchone()[0]
                    candidates.append((last_attempt or "", iso, group, week))
    return min(candidates)[-1] if candidates else None


def run_once(db, *, read=public_transport.read_week):
    source = catalog.TUSUR["source_id"]
    if not scheduled(source):
        return {"result": "disabled"}
    configured = groups()
    if not set(configured) <= catalog.TUSUR["groups"].keys():
        raise DomainError(422, "DEAN_TUSUR_AUTO_GROUPS содержит неизвестную группу.")
    week = eligible(db, source, configured, now())
    if week is None:
        return {"result": "nothing_due"}
    result = {"source": source, "group": week.group_id, "monday": week.monday.isoformat()}
    try:
        preview = public_sync.stage_live(db, week, read=read)
    except DomainError as error:
        return {**result, "result": "deferred", "reason": error.message}
    if not preview:
        saved = public_sync.pending_live(db, week)
        preview = saved["id"] if saved else None
    if not preview:
        return {**result, "result": "unchanged"}
    if os.getenv("DEAN_TUSUR_AUTO_PUBLISH") != "1":
        return {**result, "result": "preview_ready", "preview": preview}
    try:
        report = public_sync.publish(db, preview)
    except DomainError as error:
        return {**result, "result": "review_required", "preview": preview, "reason": error.message}
    return {**result, "result": "published", "preview": preview, "diff": report}
