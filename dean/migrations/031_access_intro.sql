CREATE TABLE access_intro_dismissal (
    account_id TEXT PRIMARY KEY REFERENCES account(id) ON DELETE CASCADE,
    dismissed_at TEXT NOT NULL
);
