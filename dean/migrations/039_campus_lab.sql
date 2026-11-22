CREATE TABLE lab_door (
    id INTEGER PRIMARY KEY,
    room TEXT NOT NULL,
    recorded TEXT NOT NULL,
    delta INTEGER NOT NULL CHECK(delta IN (-1,1)),
    occupied INTEGER NOT NULL CHECK(occupied >= 0)
);
CREATE INDEX lab_door_room ON lab_door(room,id);
CREATE TABLE lab_presence (
    actor TEXT PRIMARY KEY REFERENCES account(id),
    state TEXT NOT NULL CHECK(state IN ('near','far','unknown')),
    expires TEXT NOT NULL
);
CREATE TABLE lab_lecture (
    id TEXT PRIMARY KEY,
    owner TEXT NOT NULL REFERENCES account(id),
    university_id TEXT NOT NULL,
    group_id TEXT NOT NULL,
    period_id TEXT NOT NULL,
    subject_id TEXT NOT NULL,
    title TEXT NOT NULL,
    created TEXT NOT NULL,
    ended TEXT,
    transcript TEXT,
    notes TEXT,
    homework TEXT,
    source TEXT
);
CREATE TABLE lab_segment (
    lecture TEXT NOT NULL REFERENCES lab_lecture(id),
    seq INTEGER NOT NULL,
    mime TEXT NOT NULL,
    media BLOB NOT NULL,
    PRIMARY KEY(lecture,seq)
);
ALTER TABLE lab_lecture ADD COLUMN analysis_until TEXT;
