ALTER TABLE ai_request RENAME TO ai_request_previous;
CREATE TABLE ai_request (
    owner_id TEXT NOT NULL REFERENCES account(id) ON DELETE CASCADE,
    kind TEXT NOT NULL CHECK (kind IN ('photo','assistant','lecture')),
    command_id TEXT NOT NULL,
    fingerprint TEXT NOT NULL,
    started_at TEXT NOT NULL,
    result_json TEXT,
    PRIMARY KEY (owner_id,kind,command_id)
);
INSERT INTO ai_request SELECT * FROM ai_request_previous;
DROP TABLE ai_request_previous;
CREATE INDEX ai_request_started ON ai_request(started_at);
