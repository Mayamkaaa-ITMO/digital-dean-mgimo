CREATE TABLE event_ticket (
    id TEXT PRIMARY KEY,
    reservation_id TEXT NOT NULL REFERENCES reservation(id),
    token_hash TEXT NOT NULL UNIQUE,
    request_id TEXT NOT NULL,
    issued_at TEXT NOT NULL,
    revoked_at TEXT,
    used_at TEXT,
    UNIQUE(reservation_id,request_id)
);
CREATE UNIQUE INDEX event_ticket_current ON event_ticket(reservation_id) WHERE revoked_at IS NULL;

CREATE TABLE event_ticket_check (
    id TEXT PRIMARY KEY,
    actor_id TEXT NOT NULL REFERENCES account(id),
    slot_id TEXT NOT NULL REFERENCES reservation_slot(id),
    checked_at TEXT NOT NULL
);
CREATE INDEX event_ticket_rate ON event_ticket_check(actor_id,slot_id,checked_at);

CREATE TABLE event_ticket_review (
    id TEXT PRIMARY KEY,
    ticket_id TEXT NOT NULL REFERENCES event_ticket(id),
    actor_id TEXT NOT NULL REFERENCES account(id),
    slot_id TEXT NOT NULL REFERENCES reservation_slot(id),
    expires_at TEXT NOT NULL,
    applied_at TEXT
);

CREATE TABLE event_ticket_history (
    id INTEGER PRIMARY KEY,
    reservation_id TEXT NOT NULL REFERENCES reservation(id),
    ticket_id TEXT REFERENCES event_ticket(id),
    actor_id TEXT NOT NULL REFERENCES account(id),
    action TEXT NOT NULL,
    reason TEXT NOT NULL,
    created_at TEXT NOT NULL
);
