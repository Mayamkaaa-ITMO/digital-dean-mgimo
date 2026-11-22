CREATE TABLE bookable (
    id TEXT PRIMARY KEY,
    university_id TEXT NOT NULL,
    manager_id TEXT NOT NULL,
    kind TEXT NOT NULL CHECK (kind IN ('room','consultation','sport','activity')),
    title TEXT NOT NULL,
    category TEXT NOT NULL,
    place TEXT NOT NULL,
    equipment TEXT NOT NULL,
    rules TEXT NOT NULL,
    host TEXT NOT NULL,
    UNIQUE (university_id, id),
    FOREIGN KEY (university_id, manager_id) REFERENCES account(university_id, id)
);

CREATE TABLE reservation_slot (
    id TEXT PRIMARY KEY,
    university_id TEXT NOT NULL,
    bookable_id TEXT NOT NULL,
    starts_at TEXT NOT NULL,
    ends_at TEXT NOT NULL,
    capacity INTEGER NOT NULL CHECK (capacity BETWEEN 1 AND 1000),
    closed INTEGER NOT NULL DEFAULT 0 CHECK (closed IN (0,1)),
    version INTEGER NOT NULL DEFAULT 1 CHECK (version > 0),
    CHECK (starts_at < ends_at),
    UNIQUE (university_id, id),
    FOREIGN KEY (university_id, bookable_id) REFERENCES bookable(university_id, id)
);
CREATE INDEX reservation_slot_bookable ON reservation_slot(university_id, bookable_id, starts_at);

CREATE TABLE reservation (
    id TEXT PRIMARY KEY,
    university_id TEXT NOT NULL,
    student_id TEXT NOT NULL,
    slot_id TEXT NOT NULL,
    starts_at TEXT NOT NULL,
    ends_at TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('reserved','cancelled')),
    created_at TEXT NOT NULL,
    cancelled_at TEXT,
    CHECK (starts_at < ends_at),
    FOREIGN KEY (university_id, student_id) REFERENCES account(university_id,id),
    FOREIGN KEY (university_id, slot_id) REFERENCES reservation_slot(university_id,id)
);
CREATE INDEX reservation_capacity ON reservation(university_id,slot_id,status,starts_at,ends_at);
CREATE INDEX reservation_owner ON reservation(university_id,student_id,status,starts_at);
CREATE UNIQUE INDEX reservation_identity ON reservation(university_id,student_id,slot_id,starts_at,ends_at)
WHERE status='reserved';

CREATE TABLE reservation_wait (
    id TEXT PRIMARY KEY,
    university_id TEXT NOT NULL,
    student_id TEXT NOT NULL,
    slot_id TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('waiting','notified','left')),
    queued_at TEXT NOT NULL,
    attempt INTEGER NOT NULL DEFAULT 1,
    UNIQUE (university_id, student_id, slot_id),
    FOREIGN KEY (university_id, student_id) REFERENCES account(university_id, id),
    FOREIGN KEY (university_id, slot_id) REFERENCES reservation_slot(university_id,id)
);
CREATE INDEX reservation_wait_queue ON reservation_wait(university_id,slot_id,status,queued_at);
