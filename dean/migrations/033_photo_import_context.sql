CREATE TABLE photo_import_context (
    import_id TEXT PRIMARY KEY REFERENCES calendar_import(id) ON DELETE CASCADE,
    week_of TEXT NOT NULL,
    image_key TEXT NOT NULL
);

CREATE INDEX photo_import_image ON photo_import_context(image_key);
