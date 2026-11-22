CREATE TABLE field_minute (
 session_id TEXT NOT NULL REFERENCES field_session(id), minute TEXT NOT NULL,
 low INTEGER NOT NULL, high INTEGER NOT NULL, frames INTEGER NOT NULL,
 PRIMARY KEY(session_id,minute)
);
CREATE TABLE field_reference (
 session_id TEXT PRIMARY KEY REFERENCES field_session(id), entered INTEGER NOT NULL, exited INTEGER NOT NULL, recorded TEXT NOT NULL
);
