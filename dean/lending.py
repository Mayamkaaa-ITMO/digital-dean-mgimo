from datetime import datetime, timedelta
from uuid import NAMESPACE_URL, uuid5

from . import library_queue, services as svc, workspace
from .domain import DomainError, identifier, require, role, timestamp

LOAN_SELECT = """
    SELECT l.*,b.id AS book_id,b.title,b.author,c.label,a.full_name AS student_name
    FROM library_loan l
    JOIN library_copy c ON c.id=l.copy_id AND c.university_id=l.university_id
    JOIN library_book b ON b.id=c.book_id AND b.university_id=c.university_id
    JOIN account a ON a.id=l.student_id AND a.university_id=l.university_id
    WHERE l.university_id=?
"""


def seed_library(db, university):
    for code, title, author, topic, copies in (
        ("db", "Алгоритмы реляционных баз", "Эмма Ясногорская", "Информатика", 2),
        ("circuits", "Практикум по электрическим цепям", "Лев Искров", "Энергетика", 1),
        ("teams", "Работа в проектной команде", "Ника Меридианова", "Проектирование", 0),
    ):
        book = uuid5(NAMESPACE_URL, f"dean:{university}:library:{code}").hex
        db.execute("INSERT INTO library_book VALUES (?, ?, ?, ?, ?) ON CONFLICT(id) DO NOTHING",
                   (book, university, title, author, topic))
        for number in range(1, copies + 1):
            cid = uuid5(NAMESPACE_URL, f"dean:{book}:copy:{number}").hex
            db.execute("INSERT INTO library_copy VALUES (?, ?, ?, ?) ON CONFLICT(id) DO NOTHING",
                       (cid, university, book, f"ДЕМО-{code.upper()}-{number}"))


def books(db, user, term=""):
    rows = db.execute("""
        SELECT b.*,count(c.id) AS copies,count(l.id) AS lent,count(h.id) AS reserved
        FROM library_book b LEFT JOIN library_copy c ON c.book_id=b.id AND c.university_id=b.university_id
        LEFT JOIN library_loan l ON l.copy_id=c.id AND l.university_id=c.university_id AND l.returned_at IS NULL
        LEFT JOIN library_hold h ON h.copy_id=c.id AND h.university_id=c.university_id AND h.state='offered'
        WHERE b.university_id=? GROUP BY b.id ORDER BY b.title
    """, (user["university_id"],))
    term = term[:100].strip().casefold()
    return [row for row in rows if term in (row["title"]+" "+row["author"]+" "+row["topic"]).casefold()]


def staff_students(db, user):
    role(user, "staff")
    return list(db.execute("""
        SELECT a.id,a.full_name,g.name AS group_name FROM account a
        JOIN study_group g ON g.id=a.group_id AND g.university_id=a.university_id
        JOIN staff_group staff ON staff.group_id=a.group_id AND staff.university_id=a.university_id
        WHERE a.university_id=? AND a.role='student' AND staff.staff_id=? ORDER BY a.full_name
    """, (user["university_id"], user["id"])))


def available_copies(db, user):
    role(user, "staff")
    return list(db.execute("""
        SELECT c.id,c.label,b.title,EXISTS(SELECT 1 FROM library_hold h WHERE h.copy_id=c.id AND h.state='offered') AS reserved
        FROM library_copy c
        JOIN library_book b ON b.id=c.book_id AND b.university_id=c.university_id
        WHERE c.university_id=? AND NOT EXISTS (
            SELECT 1 FROM library_loan l WHERE l.copy_id=c.id AND l.university_id=c.university_id AND l.returned_at IS NULL
        ) ORDER BY b.title,c.label
    """, (user["university_id"],)))


def loans(db, user, lid=None, *, manage=False):
    if manage:
        role(user, "staff")
        query = LOAN_SELECT + """
            AND EXISTS (SELECT 1 FROM staff_group g WHERE g.university_id=l.university_id
                        AND g.staff_id=? AND g.group_id=l.group_id)
        """
        params = (user["university_id"], user["id"])
    else:
        query = LOAN_SELECT + " AND l.student_id=?"
        params = workspace.scope(user)
    if lid:
        return require(db.execute(query + " AND l.id=?", (*params, lid)).fetchone())
    return list(db.execute(query + " ORDER BY l.returned_at IS NOT NULL,l.due_at,l.id", params))


def issue(db, user, student_id, copy_id, key, hold_id=None):
    role(user, "staff")

    def apply():
        if student_id not in {row["id"] for row in staff_students(db, user)}:
            raise DomainError(404, "Читатель недоступен.")
        if copy_id not in {row["id"] for row in available_copies(db, user)}:
            raise DomainError(409, "Экземпляр уже занят или не входит в каталог.")
        university = user["university_id"]
        library_queue.prepare_issue(db, university, copy_id, student_id, hold_id)
        student = require(db.execute("SELECT group_id FROM account WHERE university_id=? AND id=?",
                                     (university, student_id)).fetchone())
        lid, current = identifier(), timestamp()
        due_at = timestamp(datetime.fromisoformat(current) + timedelta(days=14))
        db.execute("""
            INSERT INTO library_loan (id,university_id,student_id,group_id,copy_id,issuer_id,issued_at,due_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (lid, university, student_id, student["group_id"], copy_id, user["id"], current, due_at))
        row = loans(db, user, lid, manage=True)
        svc.notify(db, university, student_id, "library", lid, f"library:{lid}:issued", "Книга выдана", row["title"], current)
        return lid

    payload = [student_id, copy_id, hold_id] if hold_id else [student_id, copy_id]
    return svc.command(db, user, "library.issue", key, payload, apply)


def update(db, user, lid, action, key):
    def apply():
        managing = action == "return"
        row = loans(db, user, lid, manage=managing)
        current = timestamp()
        if managing:
            if row["returned_at"]:
                return lid
            db.execute("UPDATE library_loan SET returned_at=? WHERE id=?", (current, lid))
            label = "Книга возвращена"
            library_queue.advance(db, row["university_id"], row["book_id"])
        else:
            if row["returned_at"] or row["renewal_count"] or current >= row["due_at"]:
                raise DomainError(409, "Доступно одно продление до наступления срока возврата.")
            if library_queue.renewal_blocked(db, row):
                raise DomainError(409, "Книгу ожидает другой читатель. Продление недоступно.")
            new_due = timestamp(datetime.fromisoformat(row["due_at"])+timedelta(days=7))
            db.execute("UPDATE library_loan SET renewal_count=1,due_at=? WHERE id=?", (new_due, lid))
            label = "Срок возврата продлён"
        svc.notify(db, row["university_id"], row["student_id"], "library", lid,
                   f"library:{lid}:{action}", label, row["title"], current)
        return lid

    return svc.command(db, user, "library.update", key, [lid, action], apply)
