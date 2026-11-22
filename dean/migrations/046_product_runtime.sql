CREATE TABLE lecture_analysis_job (
    lecture_id TEXT PRIMARY KEY REFERENCES lab_lecture(id) ON DELETE CASCADE,
    queued TEXT NOT NULL,
    cloud INTEGER NOT NULL CHECK(cloud IN (0,1)),
    use_model INTEGER NOT NULL CHECK(use_model IN (0,1))
);
CREATE TABLE campus_contact (
    id INTEGER PRIMARY KEY,
    university_id TEXT NOT NULL REFERENCES university(id),
    kind TEXT NOT NULL CHECK(kind IN ('teacher','department')),
    name TEXT NOT NULL,
    description TEXT NOT NULL,
    office TEXT NOT NULL,
    hours TEXT NOT NULL,
    email TEXT NOT NULL,
    map_place TEXT NOT NULL,
    UNIQUE(university_id,kind,name)
);
