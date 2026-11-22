CREATE TABLE calendar_filter (
    owner_id TEXT NOT NULL REFERENCES account(id) ON DELETE CASCADE,
    source_id TEXT NOT NULL,
    group_id TEXT NOT NULL,
    kind TEXT NOT NULL,
    subject TEXT NOT NULL,
    visibility TEXT NOT NULL CHECK (visibility IN ('all','study','personal')),
    PRIMARY KEY (owner_id,source_id,group_id)
);

CREATE TABLE public_group_follow (
    owner_id TEXT NOT NULL REFERENCES account(id) ON DELETE CASCADE,
    source_id TEXT NOT NULL,
    group_id TEXT NOT NULL,
    PRIMARY KEY (owner_id,source_id,group_id)
);

CREATE TABLE public_lesson_change (
    lesson_id TEXT NOT NULL REFERENCES public_lesson(id),
    version INTEGER NOT NULL,
    old_json TEXT,
    new_json TEXT NOT NULL,
    provenance TEXT NOT NULL,
    detected_at TEXT NOT NULL,
    PRIMARY KEY (lesson_id,version)
);

CREATE TABLE calendar_adjustment (
    owner_id TEXT NOT NULL REFERENCES account(id) ON DELETE CASCADE,
    lesson_id TEXT NOT NULL REFERENCES public_lesson(id),
    starts_at TEXT NOT NULL,
    ends_at TEXT NOT NULL,
    room TEXT NOT NULL,
    base_version INTEGER NOT NULL,
    revision INTEGER NOT NULL DEFAULT 1,
    PRIMARY KEY (owner_id,lesson_id),
    CHECK (starts_at < ends_at)
);
