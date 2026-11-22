CREATE TABLE assistant_proposal_next (
    id TEXT PRIMARY KEY,
    owner_id TEXT NOT NULL REFERENCES account(id) ON DELETE CASCADE,
    action TEXT NOT NULL CHECK (action IN ('create_task','finish_task','draft_certificate','book_room')),
    fields_json TEXT NOT NULL,
    caption TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    result_path TEXT
);
INSERT INTO assistant_proposal_next SELECT * FROM assistant_proposal;
DROP TABLE assistant_proposal;
ALTER TABLE assistant_proposal_next RENAME TO assistant_proposal;
