CREATE TABLE practice_offer (
    id TEXT PRIMARY KEY,
    university_id TEXT NOT NULL,
    duration_days INTEGER NOT NULL CHECK(duration_days BETWEEN 1 AND 90),
    closes_at TEXT NOT NULL,
    FOREIGN KEY(university_id,id) REFERENCES opportunity(university_id,id)
);
CREATE TABLE practice_case (
    id TEXT PRIMARY KEY,
    university_id TEXT NOT NULL,
    student_id TEXT NOT NULL,
    group_id TEXT NOT NULL,
    offer_id TEXT NOT NULL REFERENCES practice_offer(id),
    application_id TEXT NOT NULL UNIQUE REFERENCES application(id),
    supervisor_id TEXT NOT NULL,
    state TEXT NOT NULL CHECK(state IN ('active','review','changes','passed','failed')),
    starts_at TEXT NOT NULL,
    due_at TEXT NOT NULL,
    version INTEGER NOT NULL DEFAULT 1,
    UNIQUE(university_id,student_id,offer_id),
    FOREIGN KEY(university_id,student_id) REFERENCES account(university_id,id),
    FOREIGN KEY(university_id,group_id) REFERENCES study_group(university_id,id),
    FOREIGN KEY(university_id,supervisor_id) REFERENCES account(university_id,id)
);
CREATE TABLE practice_diary (
    id TEXT PRIMARY KEY,
    case_id TEXT NOT NULL REFERENCES practice_case(id),
    day TEXT NOT NULL,
    body TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(case_id,day)
);
CREATE TABLE practice_report (
    id TEXT PRIMARY KEY,
    case_id TEXT NOT NULL REFERENCES practice_case(id),
    revision INTEGER NOT NULL,
    body TEXT NOT NULL,
    submitted_at TEXT NOT NULL,
    UNIQUE(case_id,revision)
);
CREATE TABLE practice_history (
    id INTEGER PRIMARY KEY,
    case_id TEXT NOT NULL REFERENCES practice_case(id),
    actor_id TEXT NOT NULL REFERENCES account(id),
    state TEXT NOT NULL,
    note TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE achievement_review (
    entry_id TEXT PRIMARY KEY REFERENCES portfolio_entry(id) ON DELETE CASCADE,
    state TEXT NOT NULL CHECK(state IN ('pending','refused','accepted')),
    version INTEGER NOT NULL,
    requested_at TEXT NOT NULL,
    reviewer_id TEXT REFERENCES account(id),
    feedback TEXT,
    reviewed_at TEXT
);
CREATE TABLE portfolio_verification (
    entry_id TEXT PRIMARY KEY REFERENCES portfolio_entry(id) ON DELETE CASCADE,
    verifier_id TEXT NOT NULL REFERENCES account(id),
    verified_at TEXT NOT NULL,
    method TEXT NOT NULL CHECK(method IN ('staff_review','activity')),
    reservation_id TEXT UNIQUE REFERENCES reservation_attendance(reservation_id)
);
CREATE TABLE resume_document (
    id TEXT PRIMARY KEY,
    university_id TEXT NOT NULL,
    student_id TEXT NOT NULL,
    created_at TEXT NOT NULL,
    title TEXT NOT NULL,
    entry_ids_json TEXT NOT NULL,
    pdf BLOB NOT NULL,
    FOREIGN KEY(university_id,student_id) REFERENCES account(university_id,id)
);
CREATE INDEX resume_document_owner ON resume_document(university_id,student_id,created_at);

CREATE TABLE demo_course (
    id TEXT PRIMARY KEY,
    university_id TEXT NOT NULL REFERENCES university(id),
    title TEXT NOT NULL,
    category TEXT NOT NULL,
    author TEXT NOT NULL,
    description TEXT NOT NULL,
    closes_at TEXT NOT NULL,
    UNIQUE(university_id,id)
);
CREATE TABLE demo_course_lesson (
    id TEXT PRIMARY KEY,
    course_id TEXT NOT NULL REFERENCES demo_course(id),
    position INTEGER NOT NULL,
    title TEXT NOT NULL,
    content TEXT NOT NULL,
    UNIQUE(course_id,position)
);
CREATE TABLE demo_course_student (
    university_id TEXT NOT NULL,
    student_id TEXT NOT NULL,
    course_id TEXT NOT NULL,
    favorite INTEGER NOT NULL DEFAULT 0 CHECK(favorite IN (0,1)),
    enrolled_at TEXT,
    PRIMARY KEY(university_id,student_id,course_id),
    FOREIGN KEY(university_id,student_id) REFERENCES account(university_id,id),
    FOREIGN KEY(university_id,course_id) REFERENCES demo_course(university_id,id)
);
CREATE TABLE demo_course_progress (
    university_id TEXT NOT NULL,
    student_id TEXT NOT NULL,
    lesson_id TEXT NOT NULL REFERENCES demo_course_lesson(id),
    finished_at TEXT NOT NULL,
    PRIMARY KEY(university_id,student_id,lesson_id),
    FOREIGN KEY(university_id,student_id) REFERENCES account(university_id,id)
);
