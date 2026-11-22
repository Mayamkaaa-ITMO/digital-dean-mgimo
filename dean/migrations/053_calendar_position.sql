CREATE TABLE calendar_position (
    owner_id TEXT NOT NULL REFERENCES account(id) ON DELETE CASCADE,
    source_id TEXT NOT NULL,
    group_id TEXT NOT NULL,
    focus_day TEXT NOT NULL,
    view TEXT NOT NULL CHECK(view IN ('day', 'week', 'list', 'month')),
    PRIMARY KEY(owner_id, source_id, group_id)
);
