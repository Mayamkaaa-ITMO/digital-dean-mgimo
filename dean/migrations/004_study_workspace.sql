ALTER TABLE learning_result ADD COLUMN version INTEGER NOT NULL DEFAULT 1 CHECK (version >= 1);

CREATE TABLE assessment (
    id TEXT PRIMARY KEY,
    result_id TEXT NOT NULL REFERENCES learning_result(id),
    code TEXT NOT NULL,
    title TEXT NOT NULL,
    kind TEXT NOT NULL CHECK (kind IN ('coursework','exam')),
    max_points INTEGER CHECK (max_points BETWEEN 1 AND 1000),
    weight_bp INTEGER CHECK (weight_bp BETWEEN 0 AND 10000),
    points INTEGER,
    state TEXT NOT NULL CHECK (state IN ('scheduled','awaiting','graded','failed','ungraded')),
    due_at TEXT,
    UNIQUE(result_id, code),
    CHECK (points IS NULL OR (max_points IS NOT NULL AND points BETWEEN 0 AND max_points)),
    CHECK ((state IN ('graded','failed')) = (points IS NOT NULL))
);

CREATE TABLE grade_revision (
    id INTEGER PRIMARY KEY,
    result_id TEXT NOT NULL REFERENCES learning_result(id),
    actor_id TEXT NOT NULL REFERENCES account(id),
    version INTEGER NOT NULL,
    old_json TEXT NOT NULL,
    new_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(result_id, version)
);

CREATE TABLE grade_scenario (
    result_id TEXT PRIMARY KEY REFERENCES learning_result(id),
    target INTEGER CHECK (target BETWEEN 0 AND 100),
    exam_points INTEGER CHECK (exam_points BETWEEN 0 AND 1000)
);

CREATE TABLE preparation_plan (
    id TEXT PRIMARY KEY,
    university_id TEXT NOT NULL,
    owner_id TEXT NOT NULL,
    result_id TEXT NOT NULL REFERENCES learning_result(id),
    exam_at TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(university_id, owner_id, result_id),
    FOREIGN KEY (university_id, owner_id) REFERENCES account(university_id, id)
);

CREATE TABLE task (
    id TEXT PRIMARY KEY,
    university_id TEXT NOT NULL,
    owner_id TEXT NOT NULL,
    subject_id TEXT,
    plan_id TEXT REFERENCES preparation_plan(id),
    title TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    due_at TEXT,
    priority TEXT NOT NULL CHECK (priority IN ('low','normal','high')),
    state TEXT NOT NULL DEFAULT 'todo' CHECK (state IN ('todo','done')),
    version INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY (university_id, owner_id) REFERENCES account(university_id, id),
    FOREIGN KEY (university_id, subject_id) REFERENCES subject(university_id, id)
);
CREATE INDEX task_owner_due ON task (university_id, owner_id, state, due_at);

CREATE TABLE subtask (
    id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL REFERENCES task(id) ON DELETE CASCADE,
    title TEXT NOT NULL,
    done INTEGER NOT NULL DEFAULT 0 CHECK (done IN (0,1))
);
CREATE INDEX subtask_task ON subtask(task_id);

CREATE TABLE note (
    id TEXT PRIMARY KEY,
    university_id TEXT NOT NULL,
    owner_id TEXT NOT NULL,
    subject_id TEXT,
    lesson_id TEXT REFERENCES schedule_event(id),
    task_id TEXT REFERENCES task(id) ON DELETE CASCADE,
    body TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    CHECK ((subject_id IS NOT NULL) + (lesson_id IS NOT NULL) + (task_id IS NOT NULL) = 1),
    FOREIGN KEY (university_id, owner_id) REFERENCES account(university_id, id),
    FOREIGN KEY (university_id, subject_id) REFERENCES subject(university_id, id)
);
CREATE INDEX note_owner ON note(university_id, owner_id);

CREATE TABLE personal_event (
    id TEXT PRIMARY KEY,
    university_id TEXT NOT NULL,
    owner_id TEXT NOT NULL,
    title TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    location TEXT NOT NULL DEFAULT '',
    starts_at TEXT NOT NULL,
    ends_at TEXT NOT NULL,
    all_day INTEGER NOT NULL CHECK(all_day IN (0,1)),
    repeat_until TEXT,
    version INTEGER NOT NULL DEFAULT 1,
    CHECK(starts_at < ends_at),
    FOREIGN KEY (university_id, owner_id) REFERENCES account(university_id, id)
);
CREATE INDEX personal_event_owner ON personal_event(university_id, owner_id, starts_at);

CREATE TABLE personal_event_skip (
    event_id TEXT NOT NULL REFERENCES personal_event(id) ON DELETE CASCADE,
    occurrence_day TEXT NOT NULL,
    PRIMARY KEY(event_id, occurrence_day)
);

ALTER TABLE notification RENAME TO notification_v3;
CREATE TABLE notification (
    id TEXT PRIMARY KEY,
    university_id TEXT NOT NULL,
    student_id TEXT NOT NULL,
    kind TEXT NOT NULL CHECK (kind IN (
        'application','schedule','announcement','exam','grade','task','event','booking','session','library','activity'
    )),
    target_id TEXT NOT NULL,
    event_key TEXT NOT NULL,
    title TEXT NOT NULL,
    body TEXT NOT NULL,
    created_at TEXT NOT NULL,
    read_at TEXT,
    UNIQUE (university_id, student_id, event_key),
    FOREIGN KEY (university_id, student_id) REFERENCES account(university_id, id)
);
INSERT INTO notification SELECT * FROM notification_v3;
DROP TABLE notification_v3;
CREATE INDEX notification_owner ON notification(university_id, student_id, created_at);
