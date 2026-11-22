CREATE TABLE equipment_unit (
    id TEXT PRIMARY KEY,
    university_id TEXT NOT NULL,
    manager_id TEXT NOT NULL,
    label TEXT NOT NULL,
    title TEXT NOT NULL,
    pickup TEXT NOT NULL,
    return_place TEXT NOT NULL,
    maintenance INTEGER NOT NULL DEFAULT 0 CHECK(maintenance IN (0,1)),
    UNIQUE(university_id,id),
    FOREIGN KEY(university_id,manager_id) REFERENCES account(university_id,id)
);
CREATE TABLE equipment_loan (
    id TEXT PRIMARY KEY,
    university_id TEXT NOT NULL,
    student_id TEXT NOT NULL,
    unit_id TEXT NOT NULL,
    starts_at TEXT NOT NULL,
    ends_at TEXT NOT NULL,
    purpose TEXT NOT NULL,
    software TEXT NOT NULL,
    pickup TEXT NOT NULL,
    return_place TEXT NOT NULL,
    state TEXT NOT NULL CHECK(state IN ('reserved','issued','returned','cancelled')),
    created_at TEXT NOT NULL,
    CHECK(starts_at<ends_at),
    FOREIGN KEY(university_id,student_id) REFERENCES account(university_id,id),
    FOREIGN KEY(university_id,unit_id) REFERENCES equipment_unit(university_id,id)
);
CREATE INDEX equipment_windows ON equipment_loan(university_id,unit_id,state,starts_at,ends_at);
CREATE UNIQUE INDEX equipment_single_issue ON equipment_loan(unit_id) WHERE state='issued';
CREATE TABLE equipment_history (
    id INTEGER PRIMARY KEY,
    unit_id TEXT NOT NULL REFERENCES equipment_unit(id),
    loan_id TEXT REFERENCES equipment_loan(id),
    actor_id TEXT NOT NULL REFERENCES account(id),
    action TEXT NOT NULL,
    note TEXT NOT NULL,
    occurred_at TEXT NOT NULL
);

CREATE TABLE library_hold (
    id TEXT PRIMARY KEY,
    university_id TEXT NOT NULL,
    student_id TEXT NOT NULL,
    book_id TEXT NOT NULL,
    copy_id TEXT,
    state TEXT NOT NULL CHECK(state IN ('waiting','offered','expired','fulfilled','cancelled')),
    queued_at TEXT NOT NULL,
    expires_at TEXT,
    FOREIGN KEY(university_id,student_id) REFERENCES account(university_id,id),
    FOREIGN KEY(university_id,book_id) REFERENCES library_book(university_id,id),
    FOREIGN KEY(university_id,copy_id) REFERENCES library_copy(university_id,id),
    CHECK(state<>'offered' OR (copy_id IS NOT NULL AND expires_at IS NOT NULL))
);
CREATE UNIQUE INDEX library_hold_student_book ON library_hold(university_id,student_id,book_id)
    WHERE state IN ('waiting','offered');
CREATE UNIQUE INDEX library_hold_copy ON library_hold(copy_id) WHERE state='offered';
CREATE INDEX library_hold_queue ON library_hold(university_id,book_id,state,queued_at);

ALTER TABLE reservation_wait ADD COLUMN auto_from TEXT;
ALTER TABLE reservation_wait ADD COLUMN auto_to TEXT;
ALTER TABLE reservation_wait ADD COLUMN auto_until TEXT;
ALTER TABLE reservation_wait ADD COLUMN auto_start TEXT;
ALTER TABLE reservation_wait ADD COLUMN auto_end TEXT;
CREATE TABLE reservation_attendance (
    reservation_id TEXT PRIMARY KEY REFERENCES reservation(id),
    actor_id TEXT NOT NULL REFERENCES account(id),
    confirmed_at TEXT NOT NULL
);

CREATE TABLE campus_room (
    id TEXT PRIMARY KEY,
    university_id TEXT NOT NULL,
    alias TEXT NOT NULL,
    UNIQUE(university_id,alias),
    FOREIGN KEY(university_id,id) REFERENCES bookable(university_id,id)
);
ALTER TABLE schedule_event ADD COLUMN room_id TEXT REFERENCES campus_room(id);
