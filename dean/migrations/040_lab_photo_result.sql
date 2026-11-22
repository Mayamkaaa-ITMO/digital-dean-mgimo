CREATE TABLE lab_photo_result (
    draft_id TEXT PRIMARY KEY REFERENCES calendar_import(id) ON DELETE CASCADE,
    method TEXT NOT NULL
);
