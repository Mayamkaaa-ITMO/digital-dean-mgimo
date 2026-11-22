ALTER TABLE lab_lecture ADD COLUMN recording_url TEXT;
ALTER TABLE lab_lecture ADD COLUMN recording_mime TEXT;

CREATE TABLE lecture_pdf (
    lecture_id TEXT PRIMARY KEY REFERENCES lab_lecture(id) ON DELETE CASCADE,
    notes BLOB NOT NULL,
    homework BLOB NOT NULL
);
