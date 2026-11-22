ALTER TABLE service ADD COLUMN category TEXT NOT NULL DEFAULT 'study';
ALTER TABLE service ADD COLUMN target_days INTEGER NOT NULL DEFAULT 2 CHECK (target_days > 0);
ALTER TABLE service ADD COLUMN document_kind TEXT CHECK (document_kind IN ('enrolment','transcript'));

ALTER TABLE application_history RENAME TO application_history_v2;
ALTER TABLE application RENAME TO application_v2;

CREATE TABLE application (
    id TEXT PRIMARY KEY,
    university_id TEXT NOT NULL,
    student_id TEXT NOT NULL,
    group_id TEXT NOT NULL,
    service_id TEXT NOT NULL,
    details TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL CHECK (status IN (
        'draft','submitted','review','needs_changes','resubmitted','completed','ready','received','refused','cancelled'
    )),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    answers_json TEXT NOT NULL DEFAULT '{}',
    assignee_id TEXT,
    due_at TEXT,
    version INTEGER NOT NULL DEFAULT 1 CHECK (version >= 1),
    UNIQUE (university_id, id),
    FOREIGN KEY (university_id, student_id) REFERENCES account(university_id, id),
    FOREIGN KEY (university_id, group_id) REFERENCES study_group(university_id, id),
    FOREIGN KEY (university_id, service_id) REFERENCES service(university_id, id),
    FOREIGN KEY (university_id, assignee_id) REFERENCES account(university_id, id)
);
INSERT INTO application (id, university_id, student_id, group_id, service_id, details, status, created_at, updated_at)
SELECT id, university_id, student_id, group_id, service_id, details, status, created_at, updated_at
FROM application_v2;

CREATE TABLE application_history (
    id INTEGER PRIMARY KEY,
    application_id TEXT NOT NULL REFERENCES application(id),
    actor_id TEXT NOT NULL REFERENCES account(id),
    status TEXT NOT NULL,
    note TEXT NOT NULL,
    created_at TEXT NOT NULL,
    event_kind TEXT NOT NULL DEFAULT 'status' CHECK (event_kind IN ('status','saved','assignment','comment')),
    answers_json TEXT
);
INSERT INTO application_history (id, application_id, actor_id, status, note, created_at)
SELECT id, application_id, actor_id, status, note, created_at
FROM application_history_v2;

DROP TABLE application_history_v2;
DROP TABLE application_v2;
CREATE INDEX application_owner ON application(university_id, student_id, created_at);
CREATE INDEX application_queue ON application(university_id, group_id, status, service_id);
CREATE INDEX application_history_timeline ON application_history(application_id, id);

CREATE TABLE document (
    id TEXT PRIMARY KEY,
    university_id TEXT NOT NULL,
    application_id TEXT NOT NULL UNIQUE,
    issued_by TEXT NOT NULL,
    kind TEXT NOT NULL CHECK (kind IN ('enrolment','transcript')),
    title TEXT NOT NULL,
    issued_at TEXT NOT NULL,
    pdf BLOB NOT NULL,
    FOREIGN KEY (university_id, application_id) REFERENCES application(university_id, id),
    FOREIGN KEY (university_id, issued_by) REFERENCES account(university_id, id)
);
