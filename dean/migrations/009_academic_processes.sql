CREATE TABLE demo_expansion_date (
    university_id TEXT PRIMARY KEY REFERENCES university(id),
    day TEXT NOT NULL
);

CREATE TABLE retake_debt (
    id TEXT PRIMARY KEY REFERENCES learning_result(id),
    reason TEXT NOT NULL,
    conditions TEXT NOT NULL,
    admitted INTEGER NOT NULL DEFAULT 0 CHECK(admitted IN (0,1)),
    closed_at TEXT
);
CREATE TABLE retake_offer (
    id TEXT PRIMARY KEY,
    university_id TEXT NOT NULL,
    subject_id TEXT NOT NULL,
    group_id TEXT NOT NULL,
    FOREIGN KEY(university_id,id) REFERENCES bookable(university_id,id),
    FOREIGN KEY(university_id,subject_id) REFERENCES subject(university_id,id),
    FOREIGN KEY(university_id,group_id) REFERENCES study_group(university_id,id)
);
CREATE TABLE retake_booking (
    reservation_id TEXT PRIMARY KEY REFERENCES reservation(id),
    debt_id TEXT NOT NULL REFERENCES retake_debt(id),
    result TEXT CHECK(result IN ('passed','failed','absent')),
    reason TEXT,
    decided_at TEXT,
    decided_by TEXT REFERENCES account(id)
);
CREATE INDEX retake_by_debt ON retake_booking(debt_id);
CREATE TABLE retake_admission_history (
    id TEXT PRIMARY KEY,
    debt_id TEXT NOT NULL REFERENCES retake_debt(id),
    actor_id TEXT NOT NULL REFERENCES account(id),
    admitted INTEGER NOT NULL CHECK(admitted IN (0,1)),
    reason TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE curriculum_block (
    id TEXT PRIMARY KEY,
    university_id TEXT NOT NULL,
    program_id TEXT NOT NULL,
    period_id TEXT NOT NULL,
    title TEXT NOT NULL,
    rule TEXT NOT NULL CHECK(rule IN ('all','one','credits')),
    credit_target INTEGER CHECK(credit_target>0),
    uses_existing_elective INTEGER NOT NULL DEFAULT 0 CHECK(uses_existing_elective IN (0,1)),
    FOREIGN KEY(university_id,program_id) REFERENCES program(university_id,id),
    FOREIGN KEY(university_id,period_id) REFERENCES academic_period(university_id,id)
);
CREATE TABLE curriculum_block_item (
    block_id TEXT NOT NULL REFERENCES curriculum_block(id),
    item_id TEXT NOT NULL UNIQUE REFERENCES curriculum_item(id),
    PRIMARY KEY(block_id,item_id)
);
CREATE TABLE curriculum_selection (
    university_id TEXT NOT NULL,
    student_id TEXT NOT NULL,
    item_id TEXT NOT NULL,
    selected_at TEXT NOT NULL,
    PRIMARY KEY(university_id,student_id,item_id),
    FOREIGN KEY(university_id,student_id) REFERENCES account(university_id,id),
    FOREIGN KEY(university_id,item_id) REFERENCES curriculum_item(university_id,id)
);
CREATE INDEX curriculum_selection_capacity ON curriculum_selection(university_id,item_id);
CREATE TABLE curriculum_course (
    item_id TEXT PRIMARY KEY REFERENCES curriculum_item(id),
    topics TEXT NOT NULL,
    hours INTEGER NOT NULL CHECK(hours>0),
    assessment TEXT NOT NULL,
    prerequisite_subject TEXT,
    university_id TEXT NOT NULL,
    FOREIGN KEY(university_id,prerequisite_subject) REFERENCES subject(university_id,id)
);
CREATE TABLE individual_plan_approval (
    application_id TEXT PRIMARY KEY REFERENCES application(id),
    reviewer_id TEXT NOT NULL REFERENCES account(id),
    snapshot_json TEXT NOT NULL,
    approved_at TEXT NOT NULL
);

CREATE TABLE lesson_attendance (
    event_id TEXT NOT NULL REFERENCES schedule_event(id),
    student_id TEXT NOT NULL REFERENCES account(id),
    state TEXT NOT NULL CHECK(state IN ('present','absent','excused')),
    PRIMARY KEY(event_id,student_id)
);
CREATE TABLE lesson_attendance_history (
    id INTEGER PRIMARY KEY,
    event_id TEXT NOT NULL REFERENCES schedule_event(id),
    student_id TEXT NOT NULL REFERENCES account(id),
    actor_id TEXT NOT NULL REFERENCES account(id),
    state TEXT NOT NULL CHECK(state IN ('present','absent','excused')),
    reason TEXT NOT NULL,
    changed_at TEXT NOT NULL
);
