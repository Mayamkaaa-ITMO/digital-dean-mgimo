CREATE TABLE instructor_scope (
    instructor_id TEXT NOT NULL REFERENCES account(id),
    university_id TEXT NOT NULL,
    group_id TEXT NOT NULL,
    period_id TEXT NOT NULL,
    subject_id TEXT NOT NULL,
    PRIMARY KEY(instructor_id,university_id,group_id,period_id,subject_id),
    FOREIGN KEY(university_id,group_id) REFERENCES study_group(university_id,id),
    FOREIGN KEY(university_id,period_id) REFERENCES academic_period(university_id,id),
    FOREIGN KEY(university_id,subject_id) REFERENCES subject(university_id,id)
);

CREATE TABLE coursework_assignment (
    id TEXT PRIMARY KEY,
    instructor_id TEXT NOT NULL,
    university_id TEXT NOT NULL,
    group_id TEXT NOT NULL,
    period_id TEXT NOT NULL,
    subject_id TEXT NOT NULL,
    state TEXT NOT NULL CHECK (state IN ('draft','published','archived')),
    definition_version INTEGER NOT NULL DEFAULT 1,
    version INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    FOREIGN KEY(instructor_id,university_id,group_id,period_id,subject_id)
        REFERENCES instructor_scope(instructor_id,university_id,group_id,period_id,subject_id)
);

CREATE TABLE coursework_definition (
    assignment_id TEXT NOT NULL REFERENCES coursework_assignment(id),
    version INTEGER NOT NULL,
    title TEXT NOT NULL,
    description TEXT NOT NULL,
    due_at TEXT NOT NULL,
    response_mode TEXT NOT NULL CHECK (response_mode IN ('text','file','either','both')),
    late_allowed INTEGER NOT NULL CHECK (late_allowed IN (0,1)),
    attempt_limit INTEGER NOT NULL CHECK (attempt_limit BETWEEN 1 AND 8),
    max_points INTEGER NOT NULL CHECK (max_points BETWEEN 1 AND 1000),
    rubric_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY(assignment_id,version)
);

CREATE TABLE coursework_student (
    id TEXT PRIMARY KEY,
    assignment_id TEXT NOT NULL REFERENCES coursework_assignment(id),
    student_id TEXT NOT NULL REFERENCES account(id),
    definition_version INTEGER,
    task_id TEXT REFERENCES task(id) ON DELETE SET NULL,
    assessment_id TEXT UNIQUE REFERENCES assessment(id),
    status TEXT NOT NULL DEFAULT 'draft' CHECK (status IN ('draft','submitted','review','returned','graded')),
    version INTEGER NOT NULL DEFAULT 1,
    draft_text TEXT NOT NULL DEFAULT '',
    UNIQUE(assignment_id,student_id),
    FOREIGN KEY(assignment_id,definition_version) REFERENCES coursework_definition(assignment_id,version)
);
CREATE INDEX coursework_student_owner ON coursework_student(student_id,assignment_id);

CREATE TABLE coursework_attempt (
    id TEXT PRIMARY KEY,
    work_id TEXT NOT NULL REFERENCES coursework_student(id),
    number INTEGER NOT NULL,
    definition_version INTEGER NOT NULL,
    response TEXT NOT NULL,
    submitted_at TEXT NOT NULL,
    late INTEGER NOT NULL CHECK (late IN (0,1)),
    UNIQUE(work_id,number)
);

CREATE TABLE coursework_file (
    id TEXT PRIMARY KEY,
    work_id TEXT NOT NULL REFERENCES coursework_student(id),
    attempt_id TEXT REFERENCES coursework_attempt(id),
    name TEXT NOT NULL,
    media_type TEXT NOT NULL,
    content BLOB NOT NULL
);
CREATE UNIQUE INDEX coursework_draft_file ON coursework_file(work_id) WHERE attempt_id IS NULL;
CREATE UNIQUE INDEX coursework_attempt_file ON coursework_file(attempt_id) WHERE attempt_id IS NOT NULL;

CREATE TABLE coursework_review (
    attempt_id TEXT PRIMARY KEY REFERENCES coursework_attempt(id),
    instructor_id TEXT NOT NULL REFERENCES account(id),
    points_json TEXT NOT NULL,
    feedback TEXT NOT NULL,
    decision TEXT NOT NULL CHECK (decision IN ('draft','returned','graded')),
    updated_at TEXT NOT NULL
);
