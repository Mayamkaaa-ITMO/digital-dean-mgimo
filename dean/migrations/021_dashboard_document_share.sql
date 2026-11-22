CREATE TABLE dashboard_preference (
    owner_id TEXT NOT NULL REFERENCES account(id) ON DELETE CASCADE,
    mode TEXT NOT NULL CHECK (mode IN ('demo','public')),
    panels_json TEXT NOT NULL,
    shortcuts_json TEXT NOT NULL,
    version INTEGER NOT NULL DEFAULT 1,
    PRIMARY KEY (owner_id,mode)
);

CREATE TABLE document_share (
    id TEXT PRIMARY KEY,
    owner_id TEXT NOT NULL REFERENCES account(id) ON DELETE CASCADE,
    document_id TEXT NOT NULL REFERENCES document(id) ON DELETE CASCADE,
    token_hash TEXT NOT NULL UNIQUE,
    request_id TEXT NOT NULL,
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    revoked_at TEXT,
    UNIQUE(owner_id,request_id)
);
CREATE INDEX document_share_owner ON document_share(owner_id,document_id);
