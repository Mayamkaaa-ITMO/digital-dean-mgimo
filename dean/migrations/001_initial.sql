CREATE TABLE university (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    timezone TEXT NOT NULL,
    data_kind TEXT NOT NULL CHECK (data_kind = 'demo_university')
);
CREATE TABLE academic_period (
    university_id TEXT NOT NULL REFERENCES university(id),
    id TEXT NOT NULL,
    label TEXT NOT NULL,
    starts_on TEXT NOT NULL,
    ends_on TEXT NOT NULL,
    PRIMARY KEY (university_id, id),
    CHECK (starts_on <= ends_on)
);
CREATE TABLE program (
    university_id TEXT NOT NULL REFERENCES university(id),
    id TEXT NOT NULL,
    faculty TEXT NOT NULL,
    name TEXT NOT NULL,
    PRIMARY KEY (university_id, id)
);
CREATE TABLE study_group (
    university_id TEXT NOT NULL REFERENCES university(id),
    id TEXT NOT NULL,
    name TEXT NOT NULL,
    program_id TEXT NOT NULL,
    course_year INTEGER NOT NULL CHECK (course_year > 0),
    PRIMARY KEY (university_id, id),
    FOREIGN KEY (university_id, program_id) REFERENCES program(university_id, id)
);
CREATE TABLE account (
    id TEXT PRIMARY KEY,
    username TEXT UNIQUE NOT NULL COLLATE NOCASE,
    password_hash TEXT NOT NULL,
    full_name TEXT NOT NULL,
    role TEXT NOT NULL CHECK (role IN ('student','staff')),
    university_id TEXT NOT NULL REFERENCES university(id),
    group_id TEXT,
    period_id TEXT,
    UNIQUE (university_id, id),
    CHECK ((role = 'student' AND group_id IS NOT NULL AND period_id IS NOT NULL)
        OR (role = 'staff' AND group_id IS NULL AND period_id IS NULL)),
    FOREIGN KEY (university_id, group_id) REFERENCES study_group(university_id, id),
    FOREIGN KEY (university_id, period_id) REFERENCES academic_period(university_id, id)
);
CREATE TABLE staff_group (
    university_id TEXT NOT NULL,
    staff_id TEXT NOT NULL,
    group_id TEXT NOT NULL,
    PRIMARY KEY (university_id, staff_id, group_id),
    FOREIGN KEY (university_id, staff_id) REFERENCES account(university_id, id),
    FOREIGN KEY (university_id, group_id) REFERENCES study_group(university_id, id)
);
CREATE TABLE session (
    token_hash TEXT PRIMARY KEY,
    account_id TEXT NOT NULL REFERENCES account(id),
    expires_at TEXT NOT NULL
);
CREATE TABLE login_attempt (
    address TEXT NOT NULL,
    username TEXT NOT NULL,
    attempted_at TEXT NOT NULL
);
CREATE INDEX login_attempt_time ON login_attempt(attempted_at);
CREATE TABLE subject (
    university_id TEXT NOT NULL REFERENCES university(id),
    id TEXT NOT NULL,
    name TEXT NOT NULL,
    PRIMARY KEY (university_id, id)
);
CREATE TABLE learning_result (
    id TEXT PRIMARY KEY,
    university_id TEXT NOT NULL,
    student_id TEXT NOT NULL,
    period_id TEXT NOT NULL,
    subject_id TEXT NOT NULL,
    kind TEXT NOT NULL CHECK (kind IN ('exam','credit')),
    score INTEGER CHECK (score BETWEEN 0 AND 100),
    outcome TEXT CHECK (outcome IN ('passed','failed')),
    exam_at TEXT,
    UNIQUE (university_id, student_id, period_id, subject_id),
    FOREIGN KEY (university_id, student_id) REFERENCES account(university_id, id),
    FOREIGN KEY (university_id, period_id) REFERENCES academic_period(university_id, id),
    FOREIGN KEY (university_id, subject_id) REFERENCES subject(university_id, id)
);
CREATE TABLE schedule_event (
    id TEXT PRIMARY KEY,
    university_id TEXT NOT NULL,
    group_id TEXT NOT NULL,
    period_id TEXT NOT NULL,
    subject_id TEXT NOT NULL,
    starts_at TEXT NOT NULL,
    ends_at TEXT NOT NULL,
    timezone TEXT NOT NULL,
    kind TEXT NOT NULL,
    room TEXT,
    teacher TEXT,
    source_id TEXT NOT NULL,
    source_event_id TEXT,
    origin TEXT NOT NULL CHECK (origin IN ('demo_university','public','user')),
    source_url TEXT,
    observed_at TEXT,
    version INTEGER NOT NULL DEFAULT 1 CHECK (version >= 1),
    UNIQUE (university_id, source_id, group_id, period_id, source_event_id),
    CHECK (starts_at < ends_at),
    FOREIGN KEY (university_id, group_id) REFERENCES study_group(university_id, id),
    FOREIGN KEY (university_id, period_id) REFERENCES academic_period(university_id, id),
    FOREIGN KEY (university_id, subject_id) REFERENCES subject(university_id, id)
);
CREATE INDEX schedule_scope ON schedule_event(university_id, group_id, period_id, starts_at);
CREATE TABLE schedule_revision (
    university_id TEXT NOT NULL,
    event_id TEXT NOT NULL REFERENCES schedule_event(id),
    staff_id TEXT NOT NULL,
    version INTEGER NOT NULL,
    old_starts_at TEXT NOT NULL,
    old_ends_at TEXT NOT NULL,
    old_room TEXT,
    changed_at TEXT NOT NULL,
    PRIMARY KEY (event_id, version),
    FOREIGN KEY (university_id, staff_id) REFERENCES account(university_id, id)
);
CREATE TABLE service (
    university_id TEXT NOT NULL REFERENCES university(id),
    id TEXT NOT NULL,
    name TEXT NOT NULL,
    description TEXT NOT NULL,
    placeholder TEXT NOT NULL,
    PRIMARY KEY (university_id, id)
);
CREATE TABLE application (
    id TEXT PRIMARY KEY,
    university_id TEXT NOT NULL,
    student_id TEXT NOT NULL,
    group_id TEXT NOT NULL,
    service_id TEXT NOT NULL,
    details TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('submitted','review','completed')),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY (university_id, student_id) REFERENCES account(university_id, id),
    FOREIGN KEY (university_id, group_id) REFERENCES study_group(university_id, id),
    FOREIGN KEY (university_id, service_id) REFERENCES service(university_id, id)
);
CREATE INDEX application_owner ON application(university_id, student_id, created_at);
CREATE INDEX application_queue ON application(university_id, group_id, status);
CREATE TABLE application_history (
    id INTEGER PRIMARY KEY,
    application_id TEXT NOT NULL REFERENCES application(id),
    actor_id TEXT NOT NULL REFERENCES account(id),
    status TEXT NOT NULL CHECK (status IN ('submitted','review','completed')),
    note TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE (application_id, status)
);
CREATE TABLE announcement (
    id TEXT PRIMARY KEY,
    university_id TEXT NOT NULL,
    group_id TEXT NOT NULL,
    staff_id TEXT NOT NULL,
    title TEXT NOT NULL,
    body TEXT NOT NULL,
    created_at TEXT NOT NULL,
    FOREIGN KEY (university_id, group_id) REFERENCES study_group(university_id, id),
    FOREIGN KEY (university_id, staff_id) REFERENCES account(university_id, id)
);
CREATE TABLE notification (
    id TEXT PRIMARY KEY,
    university_id TEXT NOT NULL,
    student_id TEXT NOT NULL,
    kind TEXT NOT NULL CHECK (kind IN ('application','schedule','announcement','exam')),
    target_id TEXT NOT NULL,
    event_key TEXT NOT NULL,
    title TEXT NOT NULL,
    body TEXT NOT NULL,
    created_at TEXT NOT NULL,
    read_at TEXT,
    UNIQUE (university_id, student_id, event_key),
    FOREIGN KEY (university_id, student_id) REFERENCES account(university_id, id)
);
CREATE INDEX notification_owner ON notification(university_id, student_id, created_at);
CREATE TABLE command (
    university_id TEXT NOT NULL,
    actor_id TEXT NOT NULL,
    operation TEXT NOT NULL,
    request_id TEXT NOT NULL,
    fingerprint TEXT NOT NULL,
    result_id TEXT NOT NULL,
    PRIMARY KEY (university_id, actor_id, operation, request_id),
    FOREIGN KEY (university_id, actor_id) REFERENCES account(university_id, id)
);
