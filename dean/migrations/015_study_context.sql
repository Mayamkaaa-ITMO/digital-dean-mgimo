CREATE TABLE study_preference (
    owner_id TEXT PRIMARY KEY REFERENCES account(id) ON DELETE CASCADE,
    main_source TEXT NOT NULL DEFAULT '',
    main_group TEXT NOT NULL DEFAULT '',
    revision INTEGER NOT NULL DEFAULT 0,
    CHECK ((main_source = '') = (main_group = ''))
);

CREATE TABLE study_group_option (
    owner_id TEXT NOT NULL REFERENCES account(id) ON DELETE CASCADE,
    source_id TEXT NOT NULL,
    group_id TEXT NOT NULL,
    favorite INTEGER NOT NULL DEFAULT 0 CHECK (favorite IN (0, 1)),
    focus_day TEXT,
    PRIMARY KEY (owner_id, source_id, group_id),
    CHECK ((source_id = '') = (group_id = ''))
);

CREATE TABLE personal_subject (
    id TEXT PRIMARY KEY,
    owner_id TEXT NOT NULL REFERENCES account(id) ON DELETE CASCADE,
    title TEXT NOT NULL,
    note TEXT NOT NULL DEFAULT '',
    source_id TEXT,
    group_id TEXT,
    period TEXT,
    source_title TEXT,
    revision INTEGER NOT NULL DEFAULT 1,
    CHECK ((source_id IS NULL) = (group_id IS NULL)),
    CHECK ((source_id IS NULL) = (period IS NULL)),
    CHECK ((source_id IS NULL) = (source_title IS NULL)),
    UNIQUE (owner_id, source_id, group_id, period, source_title)
);
CREATE INDEX personal_subject_owner ON personal_subject(owner_id, title);

CREATE TABLE personal_subject_task (
    task_id TEXT PRIMARY KEY REFERENCES task(id) ON DELETE CASCADE,
    subject_id TEXT NOT NULL REFERENCES personal_subject(id) ON DELETE CASCADE
);
CREATE INDEX personal_subject_tasks ON personal_subject_task(subject_id);

CREATE TABLE personal_subject_result (
    subject_id TEXT PRIMARY KEY REFERENCES personal_subject(id) ON DELETE CASCADE,
    coursework INTEGER CHECK (coursework BETWEEN 0 AND 100),
    exam_points INTEGER CHECK (exam_points BETWEEN 0 AND 100),
    exam_weight INTEGER NOT NULL CHECK (exam_weight BETWEEN 1 AND 100),
    target INTEGER CHECK (target BETWEEN 0 AND 100)
);
