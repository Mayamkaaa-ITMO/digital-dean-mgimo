CREATE TABLE telegram_pair (
    id TEXT PRIMARY KEY,
    owner_id TEXT NOT NULL UNIQUE REFERENCES account(id),
    bot TEXT NOT NULL,
    nonce_hash TEXT NOT NULL UNIQUE,
    expires_at TEXT NOT NULL,
    created_at TEXT NOT NULL,
    chat_id INTEGER,
    proof_hash TEXT,
    attempts INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE telegram_binding (
    id TEXT PRIMARY KEY,
    owner_id TEXT NOT NULL REFERENCES account(id),
    bot TEXT NOT NULL,
    chat_id INTEGER NOT NULL,
    linked_at TEXT NOT NULL,
    revoked_at TEXT,
    version INTEGER NOT NULL DEFAULT 1,
    enabled INTEGER NOT NULL DEFAULT 0 CHECK (enabled IN (0,1)),
    categories_json TEXT NOT NULL DEFAULT '[]',
    timezone TEXT NOT NULL,
    quiet_start TEXT NOT NULL DEFAULT '22:00',
    quiet_end TEXT NOT NULL DEFAULT '08:00',
    summary INTEGER NOT NULL DEFAULT 0 CHECK (summary IN (0,1))
);
CREATE UNIQUE INDEX telegram_owner ON telegram_binding(owner_id) WHERE revoked_at IS NULL;
CREATE UNIQUE INDEX telegram_chat ON telegram_binding(bot,chat_id) WHERE revoked_at IS NULL;

CREATE TABLE telegram_cursor (
    bot TEXT PRIMARY KEY,
    next_offset INTEGER NOT NULL DEFAULT 0,
    last_update_at TEXT,
    lease_id TEXT,
    lease_until TEXT,
    send_after TEXT
);

CREATE TABLE telegram_update (
    bot TEXT NOT NULL,
    update_id INTEGER NOT NULL,
    handled_at TEXT NOT NULL,
    chat_key TEXT,
    PRIMARY KEY(bot,update_id)
);

CREATE INDEX telegram_inbound_rate ON telegram_update(bot,chat_key,handled_at);

CREATE TABLE telegram_outbox (
    id TEXT PRIMARY KEY,
    bot TEXT NOT NULL,
    chat_id INTEGER NOT NULL,
    binding_id TEXT REFERENCES telegram_binding(id),
    pair_id TEXT,
    notification_id TEXT REFERENCES notification(id) ON DELETE SET NULL,
    key TEXT NOT NULL,
    kind TEXT NOT NULL,
    body TEXT NOT NULL,
    state TEXT NOT NULL DEFAULT 'pending' CHECK(state IN ('pending','sending','sent','failed','uncertain','cancelled')),
    attempts INTEGER NOT NULL DEFAULT 0,
    due_at TEXT NOT NULL,
    created_at TEXT NOT NULL,
    lease_id TEXT,
    lease_until TEXT,
    error TEXT,
    provider_message INTEGER,
    UNIQUE(bot,key)
);
CREATE INDEX telegram_outbox_due ON telegram_outbox(bot,state,due_at);
