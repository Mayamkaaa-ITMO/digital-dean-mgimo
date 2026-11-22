CREATE TABLE public_selection (
    owner_id TEXT PRIMARY KEY REFERENCES account(id) ON DELETE CASCADE,
    source_id TEXT NOT NULL,
    group_id TEXT NOT NULL
);

CREATE TABLE public_operator (
    account_id TEXT PRIMARY KEY REFERENCES account(id) ON DELETE CASCADE
);

CREATE TABLE public_week (
    source_id TEXT NOT NULL,
    group_id TEXT NOT NULL,
    monday TEXT NOT NULL,
    revision INTEGER NOT NULL DEFAULT 0,
    provenance TEXT CHECK (provenance IN ('snapshot', 'live', 'synthetic')),
    observed_at TEXT,
    published_at TEXT,
    etag TEXT,
    PRIMARY KEY (source_id, group_id, monday)
);

CREATE TABLE public_lesson (
    id TEXT PRIMARY KEY,
    source_id TEXT NOT NULL,
    group_id TEXT NOT NULL,
    monday TEXT NOT NULL,
    external_id TEXT NOT NULL,
    subject TEXT NOT NULL,
    kind TEXT NOT NULL,
    starts_at TEXT NOT NULL,
    ends_at TEXT NOT NULL,
    rooms_json TEXT NOT NULL,
    cancelled INTEGER NOT NULL DEFAULT 0 CHECK (cancelled IN (0,1)),
    version INTEGER NOT NULL DEFAULT 1,
    UNIQUE (source_id, group_id, monday, external_id),
    FOREIGN KEY (source_id,group_id,monday) REFERENCES public_week(source_id,group_id,monday)
);
CREATE INDEX public_calendar ON public_lesson(source_id,group_id,starts_at);

CREATE TABLE public_note (
    owner_id TEXT NOT NULL REFERENCES account(id) ON DELETE CASCADE,
    lesson_id TEXT NOT NULL REFERENCES public_lesson(id),
    body TEXT NOT NULL,
    remind INTEGER NOT NULL DEFAULT 0 CHECK (remind IN (0,1)),
    updated_at TEXT NOT NULL,
    PRIMARY KEY (owner_id, lesson_id)
);

CREATE TABLE public_preview (
    id TEXT PRIMARY KEY,
    source_id TEXT NOT NULL,
    group_id TEXT NOT NULL,
    monday TEXT NOT NULL,
    base_revision INTEGER NOT NULL,
    events_json TEXT NOT NULL,
    provenance TEXT NOT NULL CHECK (provenance IN ('snapshot', 'live', 'synthetic')),
    observed_at TEXT NOT NULL,
    parsed_at TEXT NOT NULL,
    applied_at TEXT,
    FOREIGN KEY (source_id,group_id,monday) REFERENCES public_week(source_id,group_id,monday)
);
CREATE INDEX public_preview_scope ON public_preview(source_id,group_id,monday,parsed_at);

CREATE TABLE public_attempt (
    id TEXT PRIMARY KEY,
    source_id TEXT NOT NULL,
    group_id TEXT NOT NULL,
    monday TEXT NOT NULL,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    result TEXT NOT NULL,
    retry_at TEXT
);
