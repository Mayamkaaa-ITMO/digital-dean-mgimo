CREATE TABLE ai_request (
    owner_id TEXT NOT NULL REFERENCES account(id) ON DELETE CASCADE,
    kind TEXT NOT NULL CHECK (kind IN ('photo','assistant')),
    command_id TEXT NOT NULL,
    fingerprint TEXT NOT NULL,
    started_at TEXT NOT NULL,
    result_json TEXT,
    PRIMARY KEY (owner_id,kind,command_id)
);
CREATE INDEX ai_request_started ON ai_request(started_at);

CREATE TABLE ai_budget (
    id INTEGER PRIMARY KEY CHECK (id=1),
    reserved INTEGER NOT NULL CHECK (reserved>=0)
);
INSERT INTO ai_budget VALUES (1,0);

CREATE TABLE assistant_proposal (
    id TEXT PRIMARY KEY,
    owner_id TEXT NOT NULL REFERENCES account(id) ON DELETE CASCADE,
    action TEXT NOT NULL CHECK (action IN ('create_task','finish_task','draft_certificate')),
    fields_json TEXT NOT NULL,
    caption TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    result_path TEXT
);

CREATE TABLE onboarding (
    owner_id TEXT PRIMARY KEY REFERENCES account(id) ON DELETE CASCADE,
    state TEXT NOT NULL CHECK (state IN ('completed','skipped')),
    decided_at TEXT NOT NULL
);

CREATE TABLE calendar_share (
    id TEXT PRIMARY KEY,
    owner_id TEXT NOT NULL REFERENCES account(id) ON DELETE CASCADE,
    token_hash TEXT NOT NULL UNIQUE,
    request_id TEXT NOT NULL,
    monday TEXT NOT NULL,
    timezone TEXT NOT NULL,
    events_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    revoked_at TEXT,
    UNIQUE (owner_id,request_id)
);
CREATE INDEX calendar_share_owner ON calendar_share(owner_id,created_at);

ALTER TABLE telegram_outbox ADD COLUMN menu TEXT NOT NULL DEFAULT ''
    CHECK (menu IN ('','guest','student','staff','remove'));
