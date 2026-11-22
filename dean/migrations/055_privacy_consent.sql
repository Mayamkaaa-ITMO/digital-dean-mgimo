CREATE TABLE privacy_consent_document (
    version TEXT PRIMARY KEY NOT NULL,
    consent_text TEXT NOT NULL
);
CREATE TABLE privacy_consent_event (
    id INTEGER PRIMARY KEY,
    owner_id TEXT NOT NULL REFERENCES account(id) ON DELETE CASCADE,
    version TEXT NOT NULL REFERENCES privacy_consent_document(version),
    action TEXT NOT NULL CHECK (action IN ('grant','withdraw')),
    recorded_at TEXT NOT NULL
);
CREATE INDEX privacy_consent_event_owner ON privacy_consent_event(owner_id,id DESC);
