from datetime import datetime, timedelta

from . import services as svc
from .db import write
from .domain import DomainError, identifier, require, role, timestamp
from .workspace import scope

STATES = {"waiting": "В очереди", "offered": "Можно получить", "expired": "Срок резерва истёк",
          "fulfilled": "Получено", "cancelled": "Отменено"}


def holds(db, user, hid=None):
    query = """
        SELECT h.*,b.title,c.label FROM library_hold h
        JOIN library_book b ON b.id=h.book_id AND b.university_id=h.university_id
        LEFT JOIN library_copy c ON c.id=h.copy_id AND c.university_id=h.university_id
        WHERE h.university_id=? AND h.student_id=?
    """
    if hid:
        return require(db.execute(query + " AND h.id=?", (*scope(user), hid)).fetchone())
    return list(db.execute(query + " ORDER BY h.rowid DESC", scope(user)))


def advance(db, university, bid):
    now = timestamp()
    expired = list(db.execute("""
        SELECT id,student_id FROM library_hold WHERE university_id=? AND book_id=? AND state='offered' AND expires_at<=?
    """, (university, bid, now)))
    for hold in expired:
        db.execute("UPDATE library_hold SET state='expired' WHERE id=?", (hold["id"],))
        svc.notify(db, university, hold["student_id"], "library_hold", hold["id"], f'hold:{hold["id"]}:expired',
                   "Срок резерва книги истёк", "Экземпляр передан дальше в очереди или доступен в каталоге.", now)
    copies = list(db.execute("""
        SELECT c.id,c.label,b.title FROM library_copy c JOIN library_book b ON b.id=c.book_id
        AND b.university_id=c.university_id WHERE c.university_id=? AND c.book_id=?
        AND NOT EXISTS (SELECT 1 FROM library_loan l WHERE l.copy_id=c.id AND l.returned_at IS NULL)
        AND NOT EXISTS (SELECT 1 FROM library_hold h WHERE h.copy_id=c.id AND h.state='offered')
        ORDER BY c.label
    """, (university, bid)))
    waiting = list(db.execute("""
        SELECT id,student_id FROM library_hold WHERE university_id=? AND book_id=? AND state='waiting'
        ORDER BY queued_at,rowid LIMIT ?
    """, (university, bid, len(copies))))
    expires = timestamp(datetime.fromisoformat(now) + timedelta(days=2))
    for copy, hold in zip(copies, waiting):
        db.execute("UPDATE library_hold SET state='offered',copy_id=?,expires_at=? WHERE id=?",
                   (copy["id"], expires, hold["id"]))
        svc.notify(db, university, hold["student_id"], "library_hold", hold["id"], f'hold:{hold["id"]}:offered',
                   "Книга готова к получению", copy["title"], now)


def refresh_expired(db, university):
    keys = list(db.execute("""
        SELECT DISTINCT book_id FROM library_hold WHERE university_id=? AND state='offered' AND expires_at<=?
    """, (university, timestamp())))
    for row in keys:
        advance(db, university, row["book_id"])


def refresh(db, user):
    with write(db):
        refresh_expired(db, user["university_id"])


def reserve(db, user, bid, key):
    university, student = scope(user)

    def apply():
        require(db.execute("SELECT id FROM library_book WHERE university_id=? AND id=?", (university, bid)).fetchone())
        advance(db, university, bid)
        current = db.execute("""
            SELECT id FROM library_hold WHERE university_id=? AND student_id=? AND book_id=?
            AND state IN ('waiting','offered')
        """, (university, student, bid)).fetchone()
        if current:
            return current["id"]
        existing = db.execute("""
            SELECT 1 FROM library_loan l JOIN library_copy c ON c.id=l.copy_id AND c.university_id=l.university_id
            WHERE l.university_id=? AND l.student_id=? AND c.book_id=? AND l.returned_at IS NULL
        """, (university, student, bid)).fetchone()
        if existing:
            raise DomainError(409, "У тебя уже есть экземпляр этой книги на руках.")
        hid, now = identifier(), timestamp()
        db.execute("""
            INSERT INTO library_hold (id,university_id,student_id,book_id,state,queued_at)
            VALUES (?,?,?,?,'waiting',?)
        """, (hid, university, student, bid, now))
        advance(db, university, bid)
        return hid

    return svc.command(db, user, "library.reserve", key, [bid], apply)


def cancel(db, user, hid, key):
    def apply():
        row = holds(db, user, hid)
        if row["state"] == "cancelled":
            return hid
        if row["state"] not in {"waiting", "offered"} or (row["expires_at"] and row["expires_at"] <= timestamp()):
            raise DomainError(409, "Этот резерв уже завершён.")
        db.execute("UPDATE library_hold SET state='cancelled' WHERE id=?", (hid,))
        advance(db, row["university_id"], row["book_id"])
        return hid

    return svc.command(db, user, "library.hold.cancel", key, [hid], apply)


def prepare_issue(db, university, copy, student, hold_id=None):
    row = require(db.execute("SELECT book_id FROM library_copy WHERE university_id=? AND id=?", (university, copy)).fetchone())
    advance(db, university, row["book_id"])
    reserved = db.execute("""
        SELECT id,student_id FROM library_hold WHERE university_id=? AND copy_id=? AND state='offered'
    """, (university, copy)).fetchone()
    if hold_id and (not reserved or reserved["id"] != hold_id):
        raise DomainError(409, "Резерв изменился. Открой актуальный список.")
    if reserved and reserved["student_id"] != student:
        raise DomainError(409, "Этот экземпляр зарезервирован для другого читателя.")
    if reserved:
        db.execute("UPDATE library_hold SET state='fulfilled' WHERE id=?", (reserved["id"],))


def renewal_blocked(db, row):
    bid = row["book_id"]
    advance(db, row["university_id"], bid)
    return db.execute("""
        SELECT 1 FROM library_hold WHERE university_id=? AND book_id=? AND student_id<>?
        AND state IN ('waiting','offered') LIMIT 1
    """, (row["university_id"], bid, row["student_id"])).fetchone()


def staff_offers(db, user):
    role(user, "staff")
    return list(db.execute("""
        SELECT h.*,b.title,c.label,a.full_name FROM library_hold h
        JOIN library_book b ON b.id=h.book_id AND b.university_id=h.university_id
        JOIN library_copy c ON c.id=h.copy_id AND c.university_id=h.university_id
        JOIN account a ON a.id=h.student_id AND a.university_id=h.university_id
        JOIN staff_group g ON g.group_id=a.group_id AND g.university_id=a.university_id
        WHERE h.university_id=? AND g.staff_id=? AND h.state='offered'
        ORDER BY h.expires_at
    """, (user["university_id"], user["id"])))
