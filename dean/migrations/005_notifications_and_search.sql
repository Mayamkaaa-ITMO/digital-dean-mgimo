ALTER TABLE notification ADD COLUMN active INTEGER NOT NULL DEFAULT 1 CHECK (active IN (0,1));

CREATE TABLE reminder_setting (
    university_id TEXT NOT NULL,
    student_id TEXT NOT NULL,
    kind TEXT NOT NULL CHECK (kind IN ('exam','task','event')),
    enabled INTEGER NOT NULL CHECK (enabled IN (0,1)),
    PRIMARY KEY(university_id, student_id, kind),
    FOREIGN KEY (university_id, student_id) REFERENCES account(university_id, id)
);
