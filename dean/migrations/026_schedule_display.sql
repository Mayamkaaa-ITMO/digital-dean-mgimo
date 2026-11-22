CREATE TABLE schedule_display_preference (
    owner_id TEXT NOT NULL REFERENCES account(id),
    source_id TEXT NOT NULL,
    group_id TEXT NOT NULL,
    show_gaps INTEGER NOT NULL CHECK (show_gaps IN (0, 1)),
    colors_json TEXT NOT NULL,
    language_room TEXT NOT NULL DEFAULT '',
    revision INTEGER NOT NULL,
    PRIMARY KEY (owner_id, source_id, group_id)
);

CREATE TABLE schedule_display_hidden (
    owner_id TEXT NOT NULL REFERENCES account(id),
    source_id TEXT NOT NULL,
    group_id TEXT NOT NULL,
    lesson_id TEXT NOT NULL,
    lesson_version INTEGER NOT NULL,
    PRIMARY KEY (owner_id, source_id, group_id, lesson_id)
);
