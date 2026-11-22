from .domain import role
from .fixtures import demo_id


def seed(db, university):
    staff = db.execute("SELECT id FROM account WHERE university_id=? AND username='staff'", (university,)).fetchone()
    if not staff:
        return
    rooms = list(db.execute("SELECT DISTINCT room FROM schedule_event WHERE university_id=? AND room LIKE 'ДЕМО · %'",
                            (university,)))
    for row in rooms:
        alias = row["room"]
        existing = db.execute("""SELECT id FROM bookable WHERE university_id=? AND kind='room' AND place=?
            ORDER BY rowid LIMIT 1""", (university, alias)).fetchone()
        rid = existing[0] if existing else demo_id(university, "campus-room", alias)
        db.execute("""
            INSERT INTO bookable VALUES (?,?,?,'room',?,'Аудитории',?,'',
                'Карточка демонстрационной аудитории. Бронирование доступно при опубликованном окне.','')
            ON CONFLICT DO NOTHING
        """, (rid, university, staff[0], alias.removeprefix("ДЕМО · "), alias))
        db.execute("INSERT INTO campus_room VALUES (?,?,?) ON CONFLICT DO NOTHING", (rid, university, alias))
    db.execute("""
        INSERT INTO campus_room SELECT id,university_id,place FROM bookable
        WHERE university_id=? AND kind='room' AND NOT EXISTS (SELECT 1 FROM campus_room c WHERE c.id=bookable.id)
        ON CONFLICT DO NOTHING
    """, (university,))
    db.execute("""
        UPDATE schedule_event SET room_id=(SELECT c.id FROM campus_room c
        WHERE c.university_id=schedule_event.university_id AND c.alias=schedule_event.room)
        WHERE university_id=? AND room_id IS NULL
    """, (university,))


def room_id(db, university, label):
    row = db.execute("SELECT id FROM campus_room WHERE university_id=? AND alias=?", (university, label)).fetchone()
    return row[0] if row else None


def summary(db, user, now):
    role(user, "staff")
    university, actor = user["university_id"], user["id"]
    applications = db.execute("""
        SELECT count(*) AS active,count(CASE WHEN a.due_at<? THEN 1 END) AS overdue FROM application a
        JOIN staff_group g ON g.university_id=a.university_id AND g.group_id=a.group_id
        WHERE g.university_id=? AND g.staff_id=? AND a.status IN ('submitted','review','needs_changes','resubmitted')
    """, (now, university, actor)).fetchone()
    library = db.execute("""
        SELECT count(*) AS active,count(CASE WHEN l.due_at<? THEN 1 END) AS overdue FROM library_loan l
        JOIN staff_group g ON g.university_id=l.university_id AND g.group_id=l.group_id
        WHERE g.university_id=? AND g.staff_id=? AND l.returned_at IS NULL
    """, (now, university, actor)).fetchone()
    equipment = db.execute("""
        SELECT count(*) AS active,count(CASE WHEN l.ends_at<? THEN 1 END) AS overdue FROM equipment_loan l
        JOIN equipment_unit u ON u.id=l.unit_id AND u.university_id=l.university_id
        WHERE u.university_id=? AND u.manager_id=? AND l.state='issued'
    """, (now, university, actor)).fetchone()
    resources = db.execute("""
        SELECT count(DISTINCT b.id) AS resources,count(r.id) AS bookings FROM bookable b
        LEFT JOIN reservation_slot s ON s.bookable_id=b.id AND s.university_id=b.university_id
        LEFT JOIN reservation r ON r.slot_id=s.id AND r.university_id=s.university_id
            AND r.status='reserved' AND r.ends_at>?
        WHERE b.university_id=? AND b.manager_id=?
    """, (now, university, actor)).fetchone()
    return applications, library, equipment, resources
