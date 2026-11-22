CREATE TABLE task_effort (
    task_id TEXT PRIMARY KEY REFERENCES task(id) ON DELETE CASCADE,
    minutes INTEGER NOT NULL CHECK (minutes BETWEEN 1 AND 1440)
);

CREATE TABLE study_material (
    id TEXT PRIMARY KEY,
    subject_id TEXT NOT NULL REFERENCES personal_subject(id) ON DELETE CASCADE,
    title TEXT NOT NULL,
    kind TEXT NOT NULL CHECK (kind IN ('note','url','file')),
    value TEXT NOT NULL DEFAULT '',
    media_type TEXT,
    content BLOB,
    created_at TEXT NOT NULL,
    CHECK ((kind='file') = (content IS NOT NULL)),
    CHECK ((kind='file') = (media_type IS NOT NULL))
);
CREATE INDEX study_material_subject ON study_material(subject_id,created_at);

CREATE TABLE study_timer (
    id TEXT PRIMARY KEY,
    owner_id TEXT NOT NULL REFERENCES account(id) ON DELETE CASCADE,
    task_id TEXT REFERENCES task(id) ON DELETE SET NULL,
    task_title TEXT,
    state TEXT NOT NULL CHECK (state IN ('running','paused','finished')),
    seconds INTEGER NOT NULL DEFAULT 0 CHECK (seconds>=0),
    created_at TEXT NOT NULL,
    running_since TEXT,
    changed_at TEXT NOT NULL,
    finished_at TEXT,
    version INTEGER NOT NULL DEFAULT 1,
    CHECK ((state='running') = (running_since IS NOT NULL)),
    CHECK ((state='finished') = (finished_at IS NOT NULL))
);
CREATE UNIQUE INDEX study_timer_one_active ON study_timer(owner_id) WHERE state IN ('running','paused');

CREATE TABLE study_preparation (
    id TEXT PRIMARY KEY,
    owner_id TEXT NOT NULL REFERENCES account(id) ON DELETE CASCADE,
    source_id TEXT NOT NULL,
    group_id TEXT NOT NULL,
    deadline_at TEXT NOT NULL,
    exam TEXT NOT NULL,
    rules_json TEXT NOT NULL,
    blocks_json TEXT NOT NULL,
    remaining_json TEXT NOT NULL,
    state TEXT NOT NULL DEFAULT 'draft' CHECK (state IN ('draft','confirmed','cancelled')),
    version INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL
);
CREATE INDEX study_preparation_owner ON study_preparation(owner_id,created_at);

CREATE TABLE study_preparation_event (
    plan_id TEXT NOT NULL REFERENCES study_preparation(id) ON DELETE CASCADE,
    event_id TEXT PRIMARY KEY REFERENCES personal_event(id) ON DELETE CASCADE,
    task_id TEXT REFERENCES task(id) ON DELETE SET NULL
);
