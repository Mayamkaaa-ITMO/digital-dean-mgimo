CREATE TABLE curriculum_item (
    id TEXT PRIMARY KEY,
    university_id TEXT NOT NULL,
    program_id TEXT NOT NULL,
    period_id TEXT NOT NULL,
    subject_id TEXT,
    title TEXT NOT NULL,
    credits INTEGER NOT NULL CHECK(credits>0),
    kind TEXT NOT NULL CHECK(kind IN ('required','elective')),
    window_start TEXT,
    window_end TEXT,
    capacity INTEGER CHECK(capacity>0),
    CHECK ((kind='elective') = (window_start IS NOT NULL AND window_end IS NOT NULL AND capacity IS NOT NULL)),
    UNIQUE(university_id,id),
    UNIQUE(university_id,program_id,period_id,subject_id),
    FOREIGN KEY(university_id,program_id) REFERENCES program(university_id,id),
    FOREIGN KEY(university_id,period_id) REFERENCES academic_period(university_id,id),
    FOREIGN KEY(university_id,subject_id) REFERENCES subject(university_id,id)
);

CREATE TABLE curriculum_choice (
    university_id TEXT NOT NULL,
    student_id TEXT NOT NULL,
    period_id TEXT NOT NULL,
    item_id TEXT NOT NULL,
    selected_at TEXT NOT NULL,
    PRIMARY KEY(university_id,student_id,period_id),
    FOREIGN KEY(university_id,student_id) REFERENCES account(university_id,id),
    FOREIGN KEY(university_id,period_id) REFERENCES academic_period(university_id,id),
    FOREIGN KEY(university_id,item_id) REFERENCES curriculum_item(university_id,id)
);
CREATE INDEX curriculum_capacity ON curriculum_choice(university_id,item_id);

CREATE TABLE library_book (
    id TEXT PRIMARY KEY,
    university_id TEXT NOT NULL REFERENCES university(id),
    title TEXT NOT NULL,
    author TEXT NOT NULL,
    topic TEXT NOT NULL,
    UNIQUE(university_id,id)
);

CREATE TABLE library_copy (
    id TEXT PRIMARY KEY,
    university_id TEXT NOT NULL,
    book_id TEXT NOT NULL,
    label TEXT NOT NULL,
    UNIQUE(university_id,id),
    FOREIGN KEY(university_id,book_id) REFERENCES library_book(university_id,id)
);

CREATE TABLE library_loan (
    id TEXT PRIMARY KEY,
    university_id TEXT NOT NULL,
    student_id TEXT NOT NULL,
    group_id TEXT NOT NULL,
    copy_id TEXT NOT NULL,
    issuer_id TEXT NOT NULL,
    issued_at TEXT NOT NULL,
    due_at TEXT NOT NULL,
    returned_at TEXT,
    renewal_count INTEGER NOT NULL DEFAULT 0 CHECK(renewal_count IN (0,1)),
    FOREIGN KEY(university_id,student_id) REFERENCES account(university_id,id),
    FOREIGN KEY(university_id,group_id) REFERENCES study_group(university_id,id),
    FOREIGN KEY(university_id,copy_id) REFERENCES library_copy(university_id,id),
    FOREIGN KEY(university_id,issuer_id) REFERENCES account(university_id,id)
);
CREATE UNIQUE INDEX library_active_copy ON library_loan(university_id,copy_id) WHERE returned_at IS NULL;
CREATE INDEX library_loan_student ON library_loan(university_id,student_id);

CREATE TABLE portfolio_entry (
    id TEXT PRIMARY KEY,
    university_id TEXT NOT NULL,
    student_id TEXT NOT NULL,
    kind TEXT NOT NULL CHECK(kind IN ('project','achievement')),
    title TEXT NOT NULL,
    content TEXT NOT NULL,
    url TEXT NOT NULL DEFAULT '',
    version INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    FOREIGN KEY(university_id,student_id) REFERENCES account(university_id,id)
);
CREATE INDEX portfolio_entries_student ON portfolio_entry(university_id,student_id);

CREATE TABLE opportunity (
    id TEXT PRIMARY KEY,
    university_id TEXT NOT NULL REFERENCES university(id),
    title TEXT NOT NULL,
    sponsor TEXT NOT NULL,
    description TEXT NOT NULL,
    UNIQUE(university_id,id)
);

CREATE TABLE opportunity_tracker (
    university_id TEXT NOT NULL,
    student_id TEXT NOT NULL,
    opportunity_id TEXT NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('saved','preparing','sent_manual','answer','closed')),
    note TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY(university_id,student_id,opportunity_id),
    FOREIGN KEY(university_id,student_id) REFERENCES account(university_id,id),
    FOREIGN KEY(university_id,opportunity_id) REFERENCES opportunity(university_id,id)
);
