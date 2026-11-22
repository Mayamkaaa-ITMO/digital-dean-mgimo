CREATE TABLE personal_login (
    account_id TEXT PRIMARY KEY REFERENCES account(id) ON DELETE CASCADE
);

CREATE TABLE trusted_device (
    id TEXT PRIMARY KEY,
    account_id TEXT NOT NULL REFERENCES personal_login(account_id) ON DELETE CASCADE,
    secret_hash TEXT NOT NULL UNIQUE,
    previous_hash TEXT UNIQUE,
    previous_until TEXT,
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    method TEXT NOT NULL
);
CREATE INDEX trusted_device_owner ON trusted_device(account_id, expires_at);

ALTER TABLE session ADD COLUMN seen_at TEXT;
ALTER TABLE session ADD COLUMN confirmed_at TEXT;
ALTER TABLE session ADD COLUMN method TEXT NOT NULL DEFAULT 'password';
ALTER TABLE session ADD COLUMN device_id TEXT REFERENCES trusted_device(id) ON DELETE CASCADE;
CREATE INDEX session_device ON session(device_id);
UPDATE session SET seen_at = strftime('%Y-%m-%dT%H:%M:%S+00:00', 'now');

CREATE TRIGGER password_revoke_access AFTER UPDATE OF password_hash ON account
WHEN old.password_hash <> new.password_hash
BEGIN
    DELETE FROM session WHERE account_id = new.id;
    DELETE FROM trusted_device WHERE account_id = new.id;
END;
