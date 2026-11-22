ALTER TABLE support_ticket ADD COLUMN reply TEXT NOT NULL DEFAULT '';
ALTER TABLE support_ticket ADD COLUMN replied_by TEXT REFERENCES account(id);
ALTER TABLE support_ticket ADD COLUMN replied_at TEXT;
