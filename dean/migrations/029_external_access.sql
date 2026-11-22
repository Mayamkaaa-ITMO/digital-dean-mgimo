ALTER TABLE personal_login ADD COLUMN password_used INTEGER NOT NULL DEFAULT 0 CHECK (password_used IN (0,1));

CREATE TABLE identity_link (
    provider TEXT NOT NULL CHECK (provider IN ('max','vk','yandex')),
    subject_hash TEXT NOT NULL,
    account_id TEXT NOT NULL REFERENCES personal_login(account_id) ON DELETE CASCADE,
    created_at TEXT NOT NULL,
    PRIMARY KEY (provider,subject_hash),
    UNIQUE (account_id,provider)
);

CREATE TABLE max_invitation (
    token_hash TEXT PRIMARY KEY,
    account_id TEXT NOT NULL REFERENCES personal_login(account_id) ON DELETE CASCADE,
    expires_at TEXT NOT NULL
);

CREATE TABLE identity_attempt (
    token_hash TEXT PRIMARY KEY,
    provider TEXT NOT NULL CHECK (provider IN ('max','vk','yandex','key')),
    intent TEXT NOT NULL CHECK (intent IN ('login','link','confirm')),
    browser_hash TEXT NOT NULL,
    address_hash TEXT NOT NULL,
    account_id TEXT REFERENCES personal_login(account_id) ON DELETE CASCADE,
    target TEXT NOT NULL,
    remember INTEGER NOT NULL CHECK (remember IN (0,1)),
    proof TEXT NOT NULL,
    invitation_hash TEXT,
    confirmation_code TEXT,
    subject_hash TEXT,
    checks INTEGER NOT NULL DEFAULT 0,
    used INTEGER NOT NULL DEFAULT 0 CHECK (used IN (0,1)),
    expires_at TEXT NOT NULL
);
CREATE INDEX identity_attempt_address ON identity_attempt(address_hash,expires_at);
CREATE INDEX identity_attempt_expiry ON identity_attempt(expires_at);

CREATE TABLE access_key (
    id TEXT PRIMARY KEY,
    credential_id TEXT NOT NULL UNIQUE,
    public_key BLOB NOT NULL,
    account_id TEXT NOT NULL REFERENCES personal_login(account_id) ON DELETE CASCADE,
    sign_count INTEGER NOT NULL CHECK (sign_count >= 0),
    created_at TEXT NOT NULL
);
CREATE INDEX access_key_owner ON access_key(account_id);

CREATE TRIGGER password_revoke_pending_access AFTER UPDATE OF password_hash ON account
WHEN old.password_hash <> new.password_hash
BEGIN
    DELETE FROM identity_attempt WHERE account_id = new.id;
    DELETE FROM max_invitation WHERE account_id = new.id;
END;
