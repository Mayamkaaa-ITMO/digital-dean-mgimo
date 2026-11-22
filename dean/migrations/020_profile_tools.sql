CREATE TABLE session_handle (
    id TEXT PRIMARY KEY,
    token_hash TEXT NOT NULL UNIQUE REFERENCES session(token_hash) ON DELETE CASCADE
);
INSERT INTO session_handle SELECT lower(hex(randomblob(16))), token_hash FROM session;

CREATE TABLE profile_privacy (
    owner_id TEXT PRIMARY KEY REFERENCES account(id) ON DELETE CASCADE,
    hide_preview INTEGER NOT NULL CHECK (hide_preview IN (0,1))
);

CREATE TABLE personal_link (
    id TEXT PRIMARY KEY,
    owner_id TEXT NOT NULL REFERENCES account(id) ON DELETE CASCADE,
    title TEXT NOT NULL,
    url TEXT NOT NULL,
    kind TEXT NOT NULL CHECK (kind IN ('service','personal')),
    UNIQUE (owner_id,url)
);

CREATE TABLE support_ticket (
    id TEXT PRIMARY KEY,
    owner_id TEXT NOT NULL REFERENCES account(id) ON DELETE CASCADE,
    title TEXT NOT NULL,
    body TEXT NOT NULL,
    context TEXT NOT NULL,
    state TEXT NOT NULL DEFAULT 'open' CHECK (state IN ('open','review','closed')),
    version INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL
);
CREATE INDEX support_owner ON support_ticket(owner_id,created_at);

CREATE TABLE personal_restore (
    id TEXT PRIMARY KEY,
    owner_id TEXT NOT NULL REFERENCES account(id) ON DELETE CASCADE,
    payload_json TEXT NOT NULL,
    review_json TEXT NOT NULL,
    restore_settings INTEGER NOT NULL CHECK (restore_settings IN (0,1)),
    state TEXT NOT NULL DEFAULT 'draft' CHECK (state IN ('draft','applied','cancelled')),
    created_at TEXT NOT NULL
);

CREATE TABLE personal_restore_receipt (
    owner_id TEXT NOT NULL REFERENCES account(id) ON DELETE CASCADE,
    kind TEXT NOT NULL,
    external_id TEXT NOT NULL,
    local_id TEXT NOT NULL,
    fingerprint TEXT NOT NULL,
    PRIMARY KEY (owner_id,kind,external_id)
);

CREATE TABLE detached_personal_note (
    id TEXT PRIMARY KEY,
    owner_id TEXT NOT NULL REFERENCES account(id) ON DELETE CASCADE,
    title TEXT NOT NULL,
    body TEXT NOT NULL,
    created_at TEXT NOT NULL
);
