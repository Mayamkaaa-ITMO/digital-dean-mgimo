CREATE TABLE calendar_import (
    id TEXT PRIMARY KEY,
    owner_id TEXT NOT NULL REFERENCES account(id) ON DELETE CASCADE,
    origin_key TEXT NOT NULL,
    zone TEXT NOT NULL,
    rows_json TEXT NOT NULL,
    state TEXT NOT NULL DEFAULT 'draft' CHECK (state IN ('draft','confirmed','cancelled')),
    version INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL
);

CREATE TABLE imported_event (
    owner_id TEXT NOT NULL REFERENCES account(id) ON DELETE CASCADE,
    origin_key TEXT NOT NULL,
    external_id TEXT NOT NULL,
    uid TEXT NOT NULL UNIQUE,
    event_id TEXT REFERENCES personal_event(id) ON DELETE SET NULL,
    event_version INTEGER,
    values_json TEXT NOT NULL,
    cancelled INTEGER NOT NULL CHECK (cancelled IN (0,1)),
    revision INTEGER NOT NULL DEFAULT 1,
    changed_at TEXT NOT NULL,
    PRIMARY KEY (owner_id,origin_key,external_id)
);
CREATE INDEX imported_event_current ON imported_event(event_id);

CREATE TABLE calendar_feed (
    id TEXT PRIMARY KEY,
    owner_id TEXT NOT NULL REFERENCES account(id) ON DELETE CASCADE,
    token_hash TEXT NOT NULL UNIQUE,
    request_id TEXT NOT NULL,
    university_id TEXT NOT NULL,
    assigned_group TEXT NOT NULL,
    assigned_period TEXT NOT NULL,
    source_id TEXT NOT NULL,
    group_id TEXT NOT NULL,
    first_day TEXT NOT NULL,
    last_day TEXT NOT NULL,
    visibility TEXT NOT NULL CHECK (visibility IN ('all','study','personal')),
    created_at TEXT NOT NULL,
    revoked_at TEXT,
    UNIQUE(owner_id,request_id)
);

CREATE TABLE personal_calendar_cancellation (
    event_id TEXT PRIMARY KEY,
    owner_id TEXT NOT NULL REFERENCES account(id) ON DELETE CASCADE,
    stable_id TEXT NOT NULL,
    starts_at TEXT NOT NULL,
    ends_at TEXT NOT NULL,
    all_day INTEGER NOT NULL,
    repeat_until TEXT,
    version INTEGER NOT NULL,
    timezone TEXT NOT NULL,
    cancelled_at TEXT NOT NULL
);
CREATE INDEX calendar_cancellation_owner ON personal_calendar_cancellation(owner_id,starts_at);
